"""侧边栏 AI 助手 harness 测试（方案：plans/侧边栏AI助手Harness能力优化方案.md）"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent_core.approvals import (
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
from app.agent_core.permissions import Mode, PermissionEngine
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


if __name__ == "__main__":
    unittest.main()
