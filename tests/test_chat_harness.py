"""侧边栏 AI 助手 harness 测试（方案：plans/侧边栏AI助手Harness能力优化方案.md）"""

import unittest
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent_core.context import ContextBuilder
from app.agent_core.approvals import (
    APPROVAL_OUTCOME_ALWAYS,
    APPROVAL_OUTCOME_DENY,
    APPROVAL_OUTCOME_ONCE,
    ApprovalStore,
)
from app.agent_core.loop import (
    check_interrupted,
    clear_interrupt,
    request_interrupt,
)
from app.agent_core.memory import ChatMemory, _rebuild_history
from app.agent_core.permissions import (
    Mode,
    PermissionEngine,
    clear_session_permissions,
    is_session_tool_allowed,
    remember_session_allow,
)
from app.db.database import Base


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def row(role, content="", msg_id=0, tool_calls=None, tool_results=None):
    return SimpleNamespace(
        id=msg_id, role=role, content=content,
        tool_calls=tool_calls, tool_results=tool_results,
    )


class TestHistoryIntegrity(unittest.TestCase):
    """H0.1 - 出站历史必须满足 assistant(tool_calls) → tool 协议"""

    def setUp(self):
        self.db = make_db()
        self.mem = ChatMemory()
        self.sid = self.mem.get_or_create_session("h1", self.db)

    def tearDown(self):
        self.db.close()

    def test_assistant_precedes_tool_results(self):
        calls = [{"id": "c1", "type": "function",
                  "function": {"name": "get_market_overview", "arguments": "{}"}}]
        results = [{"tool_call_id": "c1", "tool_name": "get_market_overview",
                    "content": '{"ok": true}'}]
        self.mem.save_message(self.sid, "user", "查行情", db=self.db)
        self.mem.save_message(self.sid, "assistant", "", tool_calls=calls,
                              tool_results=results, db=self.db)
        self.mem.save_message(self.sid, "assistant", "行情不错", db=self.db)

        history = self.mem.get_history(self.sid, self.db)

        self.assertEqual([m["role"] for m in history],
                         ["user", "assistant", "tool", "assistant"])
        self.assertEqual(history[1]["tool_calls"][0]["id"], "c1")
        self.assertEqual(history[2]["tool_call_id"], "c1")
        self.assertEqual(history[2]["content"], '{"ok": true}')

    def test_order_uses_ids_not_created_at(self):
        """同一时间戳写入的消息也必须按写入顺序出站"""
        from app.models.chat import ChatMessage

        self.mem.save_message(self.sid, "user", "第一句", db=self.db)
        self.mem.save_message(self.sid, "assistant", "第二句", db=self.db)
        same_time = self.db.query(ChatMessage).first().created_at
        self.db.query(ChatMessage).update({"created_at": same_time})
        self.db.commit()

        history = self.mem.get_history(self.sid, self.db)

        self.assertEqual([m["content"] for m in history], ["第一句", "第二句"])

    def test_unpaired_tool_calls_dropped(self):
        calls = [{"id": "c1", "type": "function",
                  "function": {"name": "x", "arguments": "{}"}}]
        self.mem.save_message(self.sid, "user", "q", db=self.db)
        self.mem.save_message(self.sid, "assistant", "工具没跑完",
                              tool_calls=calls, tool_results=[], db=self.db)

        history = self.mem.get_history(self.sid, self.db)

        self.assertEqual(len(history), 2)
        self.assertNotIn("tool_calls", history[1])
        self.assertEqual(history[1]["content"], "工具没跑完")

    def test_partial_pairing_keeps_only_calls_with_results(self):
        calls = [
            {"id": "c1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
        ]
        results = [{"tool_call_id": "c1", "tool_name": "a", "content": "ok"}]
        self.mem.save_message(self.sid, "user", "q", db=self.db)
        self.mem.save_message(self.sid, "assistant", "", tool_calls=calls,
                              tool_results=results, db=self.db)

        history = self.mem.get_history(self.sid, self.db)

        self.assertEqual([tc["id"] for tc in history[1]["tool_calls"]], ["c1"])
        self.assertEqual([m["tool_call_id"] for m in history if m["role"] == "tool"], ["c1"])

    def test_leading_orphan_tool_message_dropped(self):
        history = _rebuild_history([
            row("tool", "孤儿结果", 1, tool_results=None),
            row("user", "问题", 2),
        ])
        self.assertEqual([m["role"] for m in history], ["user"])

    def test_only_orphan_tool_messages_returns_empty(self):
        self.assertEqual(_rebuild_history([row("tool", "孤儿", 1)]), [])


def make_loop(db):
    """最小 AgentLoop（绕过 __init__，避免真实 LLM 依赖）"""
    from app.agent_core.loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    loop.memory = ChatMemory()
    loop.registry = MagicMock()
    loop.registry.get_openai_tools.return_value = []
    loop.context_builder = MagicMock()
    loop.context_builder.build_turn_snapshot.return_value = "状态快照"
    loop.permissions = MagicMock()
    loop.client = MagicMock()
    return loop


def patch_loop_settings(st):
    st.llm_model = "mock-model"
    st.context_window_tokens = 128000
    st.compaction_threshold = 0.8
    st.context_compaction_light_ratio = 0.55
    st.compaction_min_tokens = 20000
    st.llm_stream_retries = 2
    st.llm_stream_retry_base = 0
    st.chat_approval_timeout = 30
    st.chat_max_tool_rounds = 10


def tool_call_round(name="get_market_overview", call_id="c1"):
    return [
        {"type": "tool_calls", "tool_calls": [
            {"id": call_id, "function": {"name": name, "arguments": "{}"}},
        ]},
        {"type": "done"},
    ]


class TestStopClosure(unittest.TestCase):
    """H0.2 - 停止按钮闭环：流式消费 / 审批等待 / 跨会话隔离"""

    def setUp(self):
        self.db = make_db()
        self.loop = make_loop(self.db)
        self.sid = "h02-stream"
        clear_interrupt(self.sid)

    def tearDown(self):
        clear_interrupt(self.sid)
        self.db.close()

    def test_stream_stops_mid_interrupt(self):
        """流式中途置位中断：立即停止消费，只保留已产出的增量"""
        from app.agent_core import provider as provider_mod

        def fake_stream(*args, **kwargs):
            yield {"type": "text_delta", "content": "第一段"}
            request_interrupt(self.sid)
            yield {"type": "text_delta", "content": "不该出现"}
            yield {"type": "done"}

        with patch("app.agent_core.loop.settings") as st, \
             patch.object(provider_mod, "stream_completion", side_effect=fake_stream):
            patch_loop_settings(st)
            events = list(self.loop._iter_events("停止测试", self.sid, self.db))

        types = [ev["type"] for ev in events]
        self.assertIn("interrupted", types)
        self.assertNotIn("assistant_message", types)
        deltas = [ev["data"]["content"] for ev in events if ev["type"] == "text_delta"]
        self.assertEqual(deltas, ["第一段"])

    def test_stop_during_approval_wait_returns_interrupted(self):
        """审批等待中停止：立即解除等待，返回「用户中断」且不执行工具"""
        store = ApprovalStore()
        self.loop.permissions = PermissionEngine(mode=Mode.INTERACTIVE)
        self.loop.registry.get_tool.return_value = SimpleNamespace(risk_level="write")

        with patch("app.agent_core.loop.settings") as st, \
             patch("app.agent_core.loop.get_approval_store", return_value=store):
            patch_loop_settings(st)
            gen = self.loop._exec_tool("execute_rebalance", {}, self.db, 1, self.sid)
            ev = next(gen)
            self.assertEqual(ev["type"], "permission_required")

            request_interrupt(self.sid)
            with self.assertRaises(StopIteration) as ctx:
                next(gen)

        self.assertEqual(ctx.exception.value, {"error": "用户中断，写操作未执行"})
        self.loop.registry.execute.assert_not_called()

    def test_loop_stop_while_awaiting_approval(self):
        """完整链路：等待审批时停止 → 工具标 error，下一轮边界 yield interrupted，不再调用 LLM"""
        from app.agent_core import provider as provider_mod

        store = ApprovalStore()
        self.loop.permissions = PermissionEngine(mode=Mode.INTERACTIVE)
        self.loop.registry.get_tool.return_value = SimpleNamespace(risk_level="write")

        rounds = [
            [{"type": "tool_calls", "tool_calls": [
                {"id": "c1", "function": {"name": "execute_rebalance", "arguments": "{}"}},
            ]}, {"type": "done"}],
            [{"type": "text_delta", "content": "不该出现"}, {"type": "done"}],
        ]

        with patch("app.agent_core.loop.settings") as st, \
             patch("app.agent_core.loop.get_approval_store", return_value=store), \
             patch.object(provider_mod, "stream_completion",
                          side_effect=[iter(r) for r in rounds]) as sc:
            patch_loop_settings(st)
            events = []
            for ev in self.loop._iter_events("跑写操作", self.sid, self.db):
                events.append(ev)
                if ev["type"] == "permission_required":
                    request_interrupt(self.sid)

        types = [ev["type"] for ev in events]
        self.assertIn("interrupted", types)
        self.assertNotIn("assistant_message", types)
        finished = [ev for ev in events if ev["type"] == "tool_finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["data"]["status"], "error")
        self.loop.registry.execute.assert_not_called()
        self.assertEqual(sc.call_count, 1)

    def test_interrupt_clears_pending_approvals(self):
        """中断会清空该会话的待审批，审批卡片不再可被 resolve"""
        store = ApprovalStore()
        req = store.create("execute_rebalance", {}, timeout=30, session_id=self.sid)

        with patch("app.agent_core.loop.get_approval_store", return_value=store):
            request_interrupt(self.sid)

        self.assertTrue(req.event.is_set())
        self.assertEqual(req.outcome, APPROVAL_OUTCOME_DENY)
        self.assertFalse(store.resolve(req.request_id, APPROVAL_OUTCOME_ONCE))

    def test_interrupt_isolated_between_sessions(self):
        """一个会话停止不清除另一个会话的待审批与中断状态"""
        store = ApprovalStore()
        req_a = store.create("execute_rebalance", {}, timeout=30, session_id="h02-iso-a")
        req_b = store.create("execute_rebalance", {}, timeout=30, session_id="h02-iso-b")
        clear_interrupt("h02-iso-a")
        clear_interrupt("h02-iso-b")
        try:
            with patch("app.agent_core.loop.get_approval_store", return_value=store):
                request_interrupt("h02-iso-a")

            self.assertTrue(req_a.event.is_set())
            self.assertTrue(check_interrupted("h02-iso-a"))
            self.assertFalse(req_b.event.is_set())
            self.assertFalse(check_interrupted("h02-iso-b"))
            self.assertTrue(store.resolve(req_b.request_id, APPROVAL_OUTCOME_ONCE))
        finally:
            clear_interrupt("h02-iso-a")
            clear_interrupt("h02-iso-b")


class TestSessionPermissions(unittest.TestCase):
    """H0.3 - 「本会话允许」跨请求持久化，删除会话时清理"""

    def setUp(self):
        self.db = make_db()
        self.loop = make_loop(self.db)
        self.sid = "h03-session"
        clear_session_permissions(self.sid)
        clear_interrupt(self.sid)

    def tearDown(self):
        clear_session_permissions(self.sid)
        clear_interrupt(self.sid)
        self.db.close()

    def _write_tool_loop(self):
        self.loop.permissions = PermissionEngine(mode=Mode.INTERACTIVE)
        self.loop.registry.get_tool.return_value = SimpleNamespace(risk_level="write")
        self.loop.registry.execute.return_value = {"ok": True}
        return self.loop

    def test_allow_persists_across_engine_instances(self):
        eng1 = PermissionEngine(mode=Mode.INTERACTIVE)
        eng1.allow_tool_for_session("execute_rebalance", session_id=self.sid)

        eng2 = PermissionEngine(mode=Mode.INTERACTIVE)
        d = eng2.evaluate("execute_rebalance", "write", session_id=self.sid)

        self.assertTrue(d.allowed)
        self.assertEqual(d.reason, "本会话已放行")

    def test_other_sessions_not_affected(self):
        remember_session_allow(self.sid, "execute_rebalance")
        eng = PermissionEngine(mode=Mode.INTERACTIVE)

        other = eng.evaluate("execute_rebalance", "write", session_id="h03-other")
        anonymous = eng.evaluate("execute_rebalance", "write")

        self.assertTrue(other.needs_user)
        self.assertTrue(anonymous.needs_user)

    def test_always_outcome_is_shared(self):
        """审批选 always 后，下一个请求新建的 PermissionEngine 直接放行"""
        store = ApprovalStore()
        self._write_tool_loop()

        with patch("app.agent_core.loop.settings") as st, \
             patch("app.agent_core.loop.get_approval_store", return_value=store):
            patch_loop_settings(st)
            gen = self.loop._exec_tool("execute_rebalance", {}, self.db, 1, self.sid)
            ev = next(gen)
            self.assertEqual(ev["type"], "permission_required")
            store.resolve(ev["data"]["request_id"], APPROVAL_OUTCOME_ALWAYS)
            with self.assertRaises(StopIteration) as ctx:
                next(gen)

        self.assertEqual(ctx.exception.value, {"ok": True})
        self.assertTrue(is_session_tool_allowed(self.sid, "execute_rebalance"))
        fresh = PermissionEngine(mode=Mode.INTERACTIVE)
        self.assertTrue(fresh.evaluate("execute_rebalance", "write",
                                       session_id=self.sid).allowed)

    def test_auto_approve_does_not_write_session_allow(self):
        """全局自动审批只影响当次执行，不污染会话授权清单"""
        store = ApprovalStore()
        store.auto_approve = True
        self._write_tool_loop()

        with patch("app.agent_core.loop.get_approval_store", return_value=store):
            gen = self.loop._exec_tool("execute_rebalance", {}, self.db, 1, self.sid)
            with self.assertRaises(StopIteration) as ctx:
                next(gen)

        self.assertEqual(ctx.exception.value, {"ok": True})
        self.loop.registry.execute.assert_called_once()
        self.assertFalse(is_session_tool_allowed(self.sid, "execute_rebalance"))

    def test_delete_session_clears_process_state(self):
        from app.routes.chat_routes import delete_session

        sid = self.loop.memory.get_or_create_session("h03-del", self.db)
        remember_session_allow(sid, "execute_rebalance")
        request_interrupt(sid)

        resp = delete_session(sid, self.db)

        self.assertEqual(resp.code, 200)
        self.assertFalse(is_session_tool_allowed(sid, "execute_rebalance"))
        self.assertFalse(check_interrupted(sid))


class TestRoundLimitSynthesis(unittest.TestCase):
    """H1.1 - 工具轮数耗尽后强制收尾，不再静默空回复"""

    def setUp(self):
        self.db = make_db()
        self.loop = make_loop(self.db)
        self.sid = "h11-synth"
        clear_interrupt(self.sid)
        self.loop.registry.get_tool.return_value = SimpleNamespace(risk_level="read")
        self.loop.registry.execute.return_value = {"ok": True}

    def tearDown(self):
        clear_interrupt(self.sid)
        self.db.close()

    def _run(self, max_rounds, rounds):
        from app.agent_core import provider as provider_mod

        with patch("app.agent_core.loop.settings") as st, \
             patch.object(provider_mod, "stream_completion",
                          side_effect=[iter(r) for r in rounds]) as sc:
            patch_loop_settings(st)
            st.chat_max_tool_rounds = max_rounds
            events = list(self.loop._iter_events("跑满轮数", self.sid, self.db))
        return events, sc

    def test_round_limit_synthesizes_final_answer(self):
        rounds = [
            tool_call_round("a", "c1"),
            tool_call_round("b", "c2"),
            [{"type": "text_delta", "content": "汇总结论"}, {"type": "done"}],
        ]
        events, sc = self._run(2, rounds)

        types = [ev["type"] for ev in events]
        self.assertEqual(sc.call_count, 3)
        self.assertIsNone(sc.call_args.args[3])  # 收尾调用不带 tools
        self.assertEqual(events[-2]["type"], "assistant_message")
        self.assertEqual(events[-2]["data"]["content"], "汇总结论")
        self.assertTrue(events[-1]["data"]["round_limit_reached"])
        self.assertNotIn("error", types)
        persisted = self.loop.memory.get_all_messages(self.sid, self.db)
        self.assertEqual(persisted[-1]["content"], "汇总结论")

    def test_synthesis_failure_reports_round_limit(self):
        rounds = [tool_call_round("a", "c1"), [{"type": "error", "error": "boom"}]]
        events, _ = self._run(1, rounds)

        types = [ev["type"] for ev in events]
        self.assertIn("error", types)
        self.assertNotIn("assistant_message", types)
        error = next(ev for ev in events if ev["type"] == "error")
        self.assertIn("已达到工具调用上限", error["data"]["error"])
        self.assertIn("boom", error["data"]["error"])

    def test_normal_turn_has_no_synthesis(self):
        rounds = [[{"type": "text_delta", "content": "直接回答"}, {"type": "done"}]]
        events, sc = self._run(3, rounds)

        self.assertEqual(sc.call_count, 1)
        self.assertEqual(events[-2]["data"]["content"], "直接回答")
        self.assertNotIn("round_limit_reached", events[-1]["data"])


def provider_delta(content=None, tool_calls=None, reasoning_content=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    if reasoning_content is not None:
        delta.reasoning_content = reasoning_content
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None,
    )


def provider_finish(reason="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=None, finish_reason=reason)], usage=None,
    )


class TestThinkingStream(unittest.TestCase):
    """H1.2 - 跨 chunk 的 <think> 标签、原生 reasoning、tool_call id 兜底"""

    def _stream(self, chunks):
        from unittest.mock import MagicMock

        from app.agent_core import provider as provider_mod

        client = MagicMock()
        client.chat.completions.create.return_value = iter(chunks + [provider_finish()])
        return list(provider_mod.stream_completion(client, "m", []))

    def test_open_tag_split_across_chunks(self):
        events = self._stream([
            provider_delta("<thi"),
            provider_delta("nk>思考"),
            provider_delta("中"),
        ])

        thinking = "".join(e["content"] for e in events if e["type"] == "thinking_delta")
        texts = [e["content"] for e in events if e["type"] == "text_delta"]
        self.assertEqual(thinking, "思考中")
        self.assertEqual(texts, [])
        self.assertEqual(sum(1 for e in events if e["type"] == "thinking_done"), 1)

    def test_close_tag_split_across_chunks(self):
        events = self._stream([
            provider_delta("<think>思考</thi"),
            provider_delta("nk>正文"),
        ])

        types = [e["type"] for e in events]
        self.assertEqual([e["content"] for e in events if e["type"] == "thinking_delta"], ["思考"])
        self.assertEqual([e["content"] for e in events if e["type"] == "text_delta"], ["正文"])
        self.assertEqual(types.count("thinking_done"), 1)
        self.assertLess(types.index("thinking_done"), types.index("text_delta"))

    def test_same_chunk_open_and_close(self):
        """同一 chunk 内开闭标签：旧实现会把 </think> 和正文当思考内容"""
        events = self._stream([provider_delta("<think>想</think>答")])

        self.assertEqual(events[0], {"type": "thinking_delta", "content": "想"})
        self.assertEqual(events[1], {"type": "thinking_done", "content": ""})
        self.assertEqual(events[2], {"type": "text_delta", "content": "答"})

    def test_partial_tag_flushed_as_text_at_stream_end(self):
        events = self._stream([provider_delta("<thi")])

        self.assertEqual([e["content"] for e in events if e["type"] == "text_delta"], ["<thi"])
        self.assertFalse(any(e["type"] == "thinking_delta" for e in events))
        self.assertFalse(any(e["type"] == "thinking_done" for e in events))

    def test_native_reasoning_content(self):
        events = self._stream([
            provider_delta(reasoning_content="推理"),
            provider_delta("答案"),
        ])

        types = [e["type"] for e in events]
        self.assertEqual([e["content"] for e in events if e["type"] == "thinking_delta"], ["推理"])
        self.assertEqual([e["content"] for e in events if e["type"] == "text_delta"], ["答案"])
        self.assertEqual(types.count("thinking_done"), 1)
        self.assertLess(types.index("thinking_done"), types.index("text_delta"))

    def test_empty_tool_call_id_filled(self):
        import re

        events = self._stream([provider_delta(tool_calls=[
            SimpleNamespace(
                index=0, id="",
                function=SimpleNamespace(name="get_market_overview", arguments="{}"),
            ),
        ])])

        tool_event = next(e for e in events if e["type"] == "tool_calls")
        call_id = tool_event["tool_calls"][0]["id"]
        self.assertRegex(call_id, r"^call_0_[0-9a-f]{8}$")


class TestModelSelection(unittest.TestCase):
    """H1.3 - 首轮选模型即生效，会话切换同步模型"""

    def setUp(self):
        self.db = make_db()
        self.loop = make_loop(self.db)
        self.sid = "h13-model"
        clear_interrupt(self.sid)

    def tearDown(self):
        clear_interrupt(self.sid)
        self.db.close()

    def _run(self, model, session_id=None):
        from app.agent_core import provider as provider_mod

        with patch("app.agent_core.loop.settings") as st, \
             patch("app.agent_core.loop.provider.resolve_llm",
                   return_value=("https://dashscope/v1", "sk", "qwen-max")) as resolve, \
             patch("app.agent_core.loop.provider.build_openai_client",
                   return_value=MagicMock()), \
             patch.object(provider_mod, "stream_completion",
                          return_value=iter([{"type": "text_delta", "content": "好"},
                                             {"type": "done"}])) as sc:
            patch_loop_settings(st)
            events = list(self.loop._iter_events(
                "你好", session_id if session_id is not None else self.sid,
                self.db, model=model,
            ))
        return events, resolve, sc

    def test_first_turn_model_applies_to_new_session(self):
        events, resolve, sc = self._run(" qwen-max ", session_id=None)

        sid = events[0]["data"]["session_id"]
        self.assertEqual(self.loop.memory.get_session_model(sid, self.db), "qwen-max")
        resolve.assert_called_once_with("qwen-max")
        self.assertEqual(sc.call_args.args[1], "qwen-max")

    def test_empty_model_restores_default(self):
        self.loop.memory.get_or_create_session(self.sid, self.db)
        self.loop.memory.set_session_model(self.sid, "qwen-max", self.db)

        _, resolve, sc = self._run("")

        self.assertEqual(self.loop.memory.get_session_model(self.sid, self.db), "")
        resolve.assert_not_called()
        self.assertEqual(sc.call_args.args[1], "mock-model")

    def test_absent_model_keeps_session_value(self):
        self.loop.memory.get_or_create_session(self.sid, self.db)
        self.loop.memory.set_session_model(self.sid, "qwen-max", self.db)

        _, _, sc = self._run(None)

        self.assertEqual(self.loop.memory.get_session_model(self.sid, self.db), "qwen-max")
        self.assertEqual(sc.call_args.args[1], "qwen-max")

    def test_history_returns_session_model(self):
        from app.routes.chat_routes import get_chat_history

        self.loop.memory.get_or_create_session(self.sid, self.db)
        self.loop.memory.set_session_model(self.sid, "qwen-max", self.db)

        resp = get_chat_history(self.sid, self.db)

        self.assertEqual(resp.data["model"], "qwen-max")


class TestContextFreshness(unittest.TestCase):
    """H1.4 - 快照带当前视图与数据新鲜度"""

    def setUp(self):
        self.db = make_db()
        self.builder = ContextBuilder()

    def tearDown(self):
        self.db.close()

    def test_view_label_injected(self):
        snapshot = self.builder.build_turn_snapshot(self.db, view="strategies")
        self.assertIn("当前视图: 策略", snapshot)

    def test_invalid_view_ignored(self):
        snapshot = self.builder.build_turn_snapshot(self.db, view="<script>")
        self.assertNotIn("当前视图", snapshot)

    def test_freshness_dates(self):
        from app.models.etf import ETFDailyIndicator, ETFQuotation
        from app.models.portfolio import PortfolioSnapshot

        self.db.add_all([
            ETFQuotation(etf_code="510300", trade_date=date(2026, 10, 20),
                         close_price=4.0, change_pct=0.5),
            ETFDailyIndicator(etf_code="510300", trade_date=date(2026, 10, 19)),
            PortfolioSnapshot(strategy_id=1, trade_date=date(2026, 10, 18)),
        ])
        self.db.commit()

        text = self.builder._get_data_freshness(self.db)

        self.assertIn("行情 2026-10-20", text)
        self.assertIn("指标扫描 2026-10-19", text)
        self.assertIn("组合快照 2026-10-18", text)

    def _set_pipeline_logs(self, *rows):
        from app.models.task_log import TaskExecutionLog

        self.db.query(TaskExecutionLog).delete()
        for row in rows:
            self.db.add(TaskExecutionLog(**row))
        self.db.commit()

    def test_pipeline_status_states(self):
        from app.models.etf import ETFQuotation
        from app.utils.trading_calendar import now_cn

        today = now_cn().date()
        started = datetime.combine(today, time(20, 0))
        # 空库时 section 整体留空（由"系统刚初始化"兜底），先落一条行情
        self.db.add(ETFQuotation(etf_code="510300", trade_date=date(2026, 10, 20),
                                 close_price=4.0, change_pct=0.5))
        self.db.commit()

        self.assertIn("今日管道: 未运行", self.builder._get_data_freshness(self.db))

        self._set_pipeline_logs({"task_name": "daily_pipeline", "status": "running",
                                 "started_at": started})
        self.assertIn("今日管道: 运行中（开始 20:00）",
                      self.builder._get_data_freshness(self.db))

        self._set_pipeline_logs({"task_name": "daily_pipeline", "status": "success",
                                 "started_at": started, "duration_seconds": 312.0})
        self.assertIn("今日管道: 完成（20:00 启动，耗时 312s）",
                      self.builder._get_data_freshness(self.db))

        self._set_pipeline_logs({"task_name": "daily_pipeline", "status": "failed",
                                 "started_at": started, "error_message": "boom"})
        text = self.builder._get_data_freshness(self.db)
        self.assertIn("今日管道: 失败", text)
        self.assertIn("最近失败: [daily_pipeline] 20:00 boom", text)

    def test_empty_db_falls_back_to_init_message(self):
        snapshot = self.builder.build_turn_snapshot(self.db)
        self.assertIn("系统刚初始化", snapshot)

    def test_failure_reason_truncated(self):
        from app.utils.trading_calendar import now_cn

        started = datetime.combine(now_cn().date(), time(20, 0))
        self._set_pipeline_logs({"task_name": "daily_pipeline.rebalance", "status": "failed",
                                 "started_at": started, "error_message": "x" * 300})

        text = self.builder._get_data_freshness(self.db)

        self.assertIn("x" * 120, text)
        self.assertNotIn("x" * 121, text)


class TestToolMetricsUsage(unittest.TestCase):
    """H1.5 - 工具耗时与跨轮累计 token 用量"""

    def setUp(self):
        self.db = make_db()
        self.loop = make_loop(self.db)
        self.sid = "h15-metrics"
        clear_interrupt(self.sid)
        self.loop.registry.get_tool.return_value = SimpleNamespace(risk_level="read")
        self.loop.registry.execute.return_value = {"ok": True}

    def tearDown(self):
        clear_interrupt(self.sid)
        self.db.close()

    def _run(self, rounds):
        from app.agent_core import provider as provider_mod

        with patch("app.agent_core.loop.settings") as st, \
             patch("app.agent_core.loop.SessionLocal"), \
             patch.object(provider_mod, "stream_completion",
                          side_effect=[iter(r) for r in rounds]):
            patch_loop_settings(st)
            return list(self.loop._iter_events("量一下", self.sid, self.db))

    @staticmethod
    def _tool_round(*pairs):
        return [
            {"type": "tool_calls", "tool_calls": [
                {"id": call_id, "function": {"name": name, "arguments": "{}"}}
                for name, call_id in pairs
            ]},
            {"type": "done"},
        ]

    @staticmethod
    def _text_round(text, usage=None):
        events = [{"type": "text_delta", "content": text}]
        if usage is not None:
            events.append({"type": "usage", "usage": usage, "finish_reason": "stop"})
        events.append({"type": "done"})
        return events

    def test_single_tool_duration(self):
        rounds = [self._tool_round(("read_a", "c1")), self._text_round("好")]

        with patch("app.agent_core.loop.perf_counter", side_effect=[10.0, 10.123]):
            events = self._run(rounds)

        finished = next(ev for ev in events if ev["type"] == "tool_finished")
        self.assertEqual(finished["data"]["duration_ms"], 123)
        self.assertEqual(events[-1]["data"]["tool_calls"][0]["duration_ms"], 123)

    def test_parallel_tools_timed_independently(self):
        import time as _time

        def exec_by_name(name, args, db):
            _time.sleep(0.06 if name == "read_slow" else 0.01)
            return {"ok": True}

        self.loop.registry.execute.side_effect = exec_by_name
        rounds = [self._tool_round(("read_slow", "c1"), ("read_fast", "c2")),
                  self._text_round("好")]

        events = self._run(rounds)

        durations = {
            ev["data"]["tool"]: ev["data"]["duration_ms"]
            for ev in events if ev["type"] == "tool_finished"
        }
        self.assertGreaterEqual(durations["read_slow"], 50)
        self.assertGreaterEqual(durations["read_fast"], 5)
        self.assertGreater(durations["read_slow"], durations["read_fast"])

    def _usage_rounds(self):
        return [
            self._tool_round(("read_a", "c1")),
            self._text_round("结论", usage={"prompt_tokens": 150,
                                            "completion_tokens": 20,
                                            "total_tokens": 170}),
        ]

    def test_cross_round_usage_accumulates(self):
        rounds = self._usage_rounds()
        # 第一轮工具调用后也返回一次 usage
        rounds[0] = rounds[0][:-1] + [
            {"type": "usage", "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                                        "total_tokens": 110},
             "finish_reason": "tool_calls"},
            {"type": "done"},
        ]

        events = self._run(rounds)

        usage_events = [ev["data"] for ev in events if ev["type"] == "usage"]
        self.assertEqual([u["total"]["total_tokens"] for u in usage_events], [110, 280])
        self.assertEqual(usage_events[-1]["total"]["prompt_tokens"], 250)
        self.assertEqual(usage_events[-1]["total"]["completion_tokens"], 30)
        self.assertEqual(events[-1]["data"]["usage"],
                         {"prompt_tokens": 250, "completion_tokens": 30, "total_tokens": 280})

    def test_final_message_persists_cumulative_usage(self):
        rounds = self._usage_rounds()
        rounds[0] = rounds[0][:-1] + [
            {"type": "usage", "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                                        "total_tokens": 110},
             "finish_reason": "tool_calls"},
            {"type": "done"},
        ]

        self._run(rounds)

        persisted = self.loop.memory.get_all_messages(self.sid, self.db)
        final = persisted[-1]
        self.assertEqual(final["content"], "结论")
        self.assertEqual(final["usage"],
                         {"prompt_tokens": 250, "completion_tokens": 30, "total_tokens": 280})

    def test_no_usage_does_not_break(self):
        events = self._run([self._text_round("直接回答")])

        self.assertNotIn("error", [ev["type"] for ev in events])
        self.assertIsNone(events[-1]["data"]["usage"])
        persisted = self.loop.memory.get_all_messages(self.sid, self.db)
        self.assertIsNone(persisted[-1]["usage"])


if __name__ == "__main__":
    unittest.main()
