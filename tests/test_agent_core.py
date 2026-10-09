"""
Agent Core 新能力测试（P1-P6：审批/权限、docstring schema、压缩、并行、Provider、中断）
用法: source .venv/bin/activate && python test_agent_core.py
或:   pytest test_agent_core.py
"""
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
import json
import re
import threading
import time

from app.agent_core import compaction as compaction_module
from app.agent_core.permissions import Mode, PermissionEngine
from app.agent_core.approvals import (
    ApprovalStore,
    APPROVAL_OUTCOME_ONCE,
    APPROVAL_OUTCOME_ALWAYS,
    APPROVAL_OUTCOME_DENY,
)
from app.agent_core.compaction import (estimate_tokens, compaction_tier, summarize,
                                       summary_budget, _trim_fallback,
                                       SUMMARY_BUDGET_MIN, SUMMARY_BUDGET_MAX,
                                       TRIM_KEEP_USER_MESSAGES, TRIM_KEEP_ASSISTANT_MESSAGES)
from app.agent_core.memory import ChatMemory


def make_db():
    """构造内存 SQLite + 全表 schema"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_loop(db):
    """构造最小可用 AgentLoop（绕过 __init__，避免真实 LLM/DB 依赖）"""
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


def iter_events(loop, user_message, session_id, db, stream=None):
    """在 settings mock 下迭代事件流

    stream: 可选，伪造 provider.stream_completion 的事件列表（或按轮次的列表的列表）
    """
    from app.agent_core import provider as provider_mod

    with patch("app.agent_core.loop.settings") as st:
        st.llm_model = "mock-model"
        st.context_window_tokens = 128000
        st.compaction_threshold = 0.8
        st.context_compaction_light_ratio = 0.55
        st.compaction_min_tokens = 20000
        st.llm_stream_retries = 0  # 测试关闭流式重试，保持确定性
        st.llm_stream_retry_base = 1.0
        if stream is not None:
            rounds = stream if isinstance(stream[0], list) else [stream]
            with patch.object(provider_mod, "stream_completion",
                              side_effect=[iter(r) for r in rounds]):
                return list(loop._iter_events(user_message, session_id, db))
        return list(loop._iter_events(user_message, session_id, db))


def final_resp(content="ok"):
    """无工具调用的最终 LLM 响应"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[]))]
    )


def text_stream(content="ok", usage=None, finish_reason="stop"):
    """无工具调用的最终轮流式事件（provider.stream_completion 事件协议）"""
    events = [{"type": "text_delta", "content": content}]
    if usage is not None:
        events.append({"type": "usage", "usage": usage, "finish_reason": finish_reason})
    events.append({"type": "done"})
    return events


def tool_calls_stream(tcs, text=""):
    """带工具调用的轮次流式事件"""
    events = []
    if text:
        events.append({"type": "text_delta", "content": text})
    events.append({"type": "tool_calls", "tool_calls": [
        {"id": tc.id, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
        for tc in tcs
    ]})
    events.append({"type": "done"})
    return events


class TestPermissions(unittest.TestCase):
    """P1 - 权限引擎三模式判定"""

    def test_discuss_rejects_write(self):
        eng = PermissionEngine(mode=Mode.DISCUSS)
        d = eng.evaluate("execute_rebalance", "write")
        self.assertFalse(d.allowed)
        self.assertFalse(d.needs_user)

    def test_read_always_allowed(self):
        eng = PermissionEngine(mode=Mode.DISCUSS)
        d = eng.evaluate("get_market_overview", "read")
        self.assertTrue(d.allowed)

    def test_interactive_write_needs_user(self):
        eng = PermissionEngine(mode=Mode.INTERACTIVE)
        d = eng.evaluate("execute_rebalance", "write")
        self.assertFalse(d.allowed)
        self.assertTrue(d.needs_user)

    def test_auto_allows_write(self):
        eng = PermissionEngine(mode=Mode.AUTO)
        d = eng.evaluate("execute_rebalance", "write")
        self.assertTrue(d.allowed)

    def test_session_allow_tool_for_session(self):
        eng = PermissionEngine(mode=Mode.INTERACTIVE)
        eng.allow_tool_for_session("execute_rebalance")
        d = eng.evaluate("execute_rebalance", "write")
        self.assertTrue(d.allowed)
        self.assertEqual(d.reason, "本会话已放行")


class TestApprovals(unittest.TestCase):
    """P1 - 审批 store 流转"""

    def test_resolve_once(self):
        store = ApprovalStore()
        req = store.create("execute_rebalance", {"strategy_id": 1}, timeout=5)
        ok = store.resolve(req.request_id, APPROVAL_OUTCOME_ONCE)
        self.assertTrue(ok)
        self.assertEqual(store.wait(req), APPROVAL_OUTCOME_ONCE)

    def test_resolve_always(self):
        store = ApprovalStore()
        req = store.create("delete_strategy", {"strategy_id": 1}, timeout=5)
        self.assertTrue(store.resolve(req.request_id, APPROVAL_OUTCOME_ALWAYS))
        self.assertEqual(store.wait(req), APPROVAL_OUTCOME_ALWAYS)

    def test_timeout_deny(self):
        store = ApprovalStore()
        req = store.create("execute_rebalance", {}, timeout=0.2)
        self.assertEqual(store.wait(req), APPROVAL_OUTCOME_DENY)

    def test_resolve_unknown_returns_false(self):
        store = ApprovalStore()
        self.assertFalse(store.resolve("no_such_id", APPROVAL_OUTCOME_ONCE))

    def test_resolve_after_resolve_returns_false(self):
        """同一 request 只能审批一次"""
        store = ApprovalStore()
        req = store.create("pause_strategy", {}, timeout=5)
        store.resolve(req.request_id, APPROVAL_OUTCOME_ONCE)
        self.assertFalse(store.resolve(req.request_id, APPROVAL_OUTCOME_ONCE))


class TestDocstringSchema(unittest.TestCase):
    """P2 - docstring/Annotated → 工具 schema 描述"""

    def test_parse_docstring_params_chinese(self):
        from app.tools.registry import _parse_docstring_params

        def fn(limit, period):
            """查询行情

            参数:
                limit: 返回条数
                period: 周期（日/周/月）
            """

        params = _parse_docstring_params(fn)
        self.assertEqual(params["limit"], "返回条数")
        self.assertEqual(params["period"], "周期（日/周/月）")

    def test_parse_docstring_params_english(self):
        from app.tools.registry import _parse_docstring_params

        def fn(name, db):
            """doc

            Args:
                name: 工具名称
                db: 数据库
            """

        params = _parse_docstring_params(fn)
        self.assertEqual(params["name"], "工具名称")
        self.assertEqual(params["db"], "数据库")  # 解析器含 db，schema 构建时才跳过

    def test_unwrap_annotated(self):
        from typing import Annotated
        from app.tools.registry import _unwrap_annotated

        base, desc = _unwrap_annotated(Annotated[str, "ETF代码"])
        self.assertIs(base, str)
        self.assertEqual(desc, "ETF代码")
        base, desc = _unwrap_annotated(int)
        self.assertIs(base, int)
        self.assertIsNone(desc)

    def test_tool_decorator_schema_and_risk(self):
        """tool() 装饰器：description 进 schema、_WRITE_TOOLS 表归类风险"""
        from app.tools.registry import tool, _TOOL_REGISTRY, _WRITE_TOOLS

        @tool(name="__test_read_tool", description="测试只读")
        def read_tool(db, limit: int = 50, keyword: str = ""):
            """读取

            参数:
                keyword: 关键词
            """

        _WRITE_TOOLS.add("__test_write_tool")

        @tool(name="__test_write_tool", description="测试写入")
        def write_tool(db, strategy_id: int = 1):
            """写入"""

        try:
            read_schema = _TOOL_REGISTRY["__test_read_tool"].to_openai_schema()["function"]
            self.assertEqual(read_schema["parameters"]["properties"]["keyword"]["description"], "关键词")
            self.assertEqual(read_schema["parameters"]["properties"]["limit"]["default"], 50)
            self.assertEqual(_TOOL_REGISTRY["__test_read_tool"].risk_level, "read")

            write_def = _TOOL_REGISTRY["__test_write_tool"]
            self.assertEqual(write_def.risk_level, "write")
            self.assertTrue(write_def.requires_approval)
        finally:
            _TOOL_REGISTRY.pop("__test_read_tool", None)
            _TOOL_REGISTRY.pop("__test_write_tool", None)
            _WRITE_TOOLS.discard("__test_write_tool")


class TestCompaction(unittest.TestCase):
    """P3 - 上下文压缩"""

    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens([{"role": "user", "content": "a" * 4000}]), 1000)

    def test_cjk_messages_are_estimated_higher_than_chars_div_4(self):
        """同一批中文消息：新估算必须高于旧的「字符/4」口径，否则压缩永远来不及"""
        text = "请把沪深300与中证白酒的近5日动量对比一下，并给出换仓建议，注意最短持有期5天。"
        legacy = len(text) // 4
        self.assertGreater(estimate_tokens([{"role": "user", "content": text}]), legacy)

    def test_tool_call_arguments_are_counted(self):
        """ReAct 一轮的 arguments 常常比正文长，漏算会让估算系统性偏低"""
        pure_text = estimate_tokens([{"role": "assistant", "content": ""}])
        with_calls = estimate_tokens([
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "1", "type": "function",
                             "function": {"name": "run_multi_agent_analysis",
                                          "arguments": json.dumps({"codes": ["512880"] * 8})}}]},
        ])
        self.assertEqual(pure_text, 0)
        self.assertGreater(with_calls, 0)

    def test_compaction_tier_boundaries(self):
        self.assertIsNone(compaction_tier(1000, 128000, 20000, 0.55, 0.8))      # 低于 min_tokens
        self.assertIsNone(compaction_tier(70399, 128000, 20000, 0.55, 0.8))     # 未到轻度线
        self.assertEqual(compaction_tier(70400, 128000, 20000, 0.55, 0.8), "light")
        self.assertEqual(compaction_tier(102400, 128000, 20000, 0.55, 0.8), "heavy")
        self.assertIsNone(compaction_tier(102400, 128000, 200000, 0.55, 0.8))   # min_tokens 更高
        self.assertIsNone(compaction_tier(102400, 0, 20000, 0.55, 0.8))         # 窗口配置异常

    def test_summary_budget_scales_with_history(self):
        self.assertEqual(summary_budget("x" * 100), SUMMARY_BUDGET_MIN)
        self.assertEqual(summary_budget("x" * 1500), 300)
        self.assertEqual(summary_budget("x" * 100000), SUMMARY_BUDGET_MAX)

    def test_summarize_prompt_carries_dynamic_budget(self):
        prompts = []

        def capture(msgs):
            prompts.append(msgs[1]["content"])
            return "摘要"

        summarize([{"role": "user", "content": "短" * 100}], capture)
        summarize([{"role": "user", "content": "长" * 2000}], capture)
        budgets = [int(re.search(r"不超过(\d+)字", p).group(1)) for p in prompts]
        self.assertEqual(budgets[0], SUMMARY_BUDGET_MIN)   # 小对话不必写长摘要
        self.assertGreater(budgets[1], budgets[0])         # 大对话预算随之增大

    def test_light_tier_summarizes_only_earliest_half(self):
        bodies = []

        def capture(msgs):
            bodies.append(msgs[1]["content"])
            return "摘要"

        msgs = [{"role": "user", "content": f"第{i}条"} for i in range(6)]
        summarize(msgs, capture, tier="light")
        self.assertIn("第0条", bodies[0])
        self.assertIn("第2条", bodies[0])
        self.assertNotIn("第3条", bodies[0])   # 后半段保持原文，不进摘要

        bodies.clear()
        summarize(msgs, capture, tier="heavy")
        self.assertIn("第5条", bodies[0])      # 重度全量摘要

    def test_prior_summary_is_merged_into_new_summary(self):
        """已有摘要必须续写进新摘要，否则每轮压缩都丢历史（摘要是唯一的历史载体）"""
        bodies = []
        summarize([{"role": "user", "content": "新话题"}],
                  lambda msgs: (bodies.append(msgs[1]["content"]), "摘要")[1],
                  prior_summary="早前讨论了白酒仓位")
        self.assertIn("早前讨论了白酒仓位", bodies[0])
        self.assertIn("新话题", bodies[0])

    def test_summarize_ok(self):
        s = summarize([{"role": "user", "content": "长文本" * 10}], lambda msgs: "结构化摘要")
        self.assertEqual(s, "结构化摘要")

    def test_summarize_fallback_on_failure(self):
        def boom(msgs):
            raise RuntimeError("llm down")

        s = summarize([{"role": "user", "content": "问题"}], boom)
        self.assertTrue(s)

    def test_summarize_fallback_keeps_prior_summary(self):
        def boom(msgs):
            raise RuntimeError("llm down")

        s = summarize([{"role": "user", "content": "问题"}], boom, prior_summary="既有摘要")
        self.assertIn("既有摘要", s)

    def test_trim_fallback_keeps_recent_assistant_actions(self):
        """降级 trim 也要保留最近助手结论，否则 LLM 会重复查同样的东西"""
        msgs = [{"role": "user", "content": f"u{i}"} for i in range(20)]
        msgs += [{"role": "assistant", "content": f"a{i}"} for i in range(10)]
        text = _trim_fallback(msgs)
        self.assertEqual(text.count("- 用户: u"), TRIM_KEEP_USER_MESSAGES)
        self.assertEqual(text.count("- 助手: a"), TRIM_KEEP_ASSISTANT_MESSAGES)
        self.assertIn("最近的推理与结论", text)
        self.assertIn("a9", text)
        self.assertNotIn("a5", text)

    def test_loop_picks_light_tier_below_heavy_ratio(self):
        db = make_db()
        loop = make_loop(db)
        sid = loop.memory.get_or_create_session("c3", db)
        for _ in range(20):
            loop.memory.save_message(sid, "user", "中" * 360, db=db)
            loop.memory.save_message(sid, "assistant", "答" * 360, db=db)

        captured = {}

        def fake_summarize(msgs, complete, tier="heavy", prior_summary=""):
            captured["tier"] = tier
            return "摘要内容"

        with patch("app.agent_core.loop.settings") as st, \
             patch("app.agent_core.provider.stream_completion") as sc, \
             patch.object(compaction_module, "summarize", side_effect=fake_summarize):
            st.llm_model = "m"
            # 40 条 × 360 汉字 ≈ 9600 token，窗口 16000 → 0.6 介于轻度与重度之间
            st.context_window_tokens = 16000
            st.compaction_threshold = 0.8
            st.context_compaction_light_ratio = 0.55
            st.compaction_min_tokens = 1000
            st.llm_stream_retries = 0
            st.llm_stream_retry_base = 1.0
            sc.return_value = iter(text_stream("好"))
            events = list(loop._iter_events("你好", "c3", db))

        self.assertEqual(captured["tier"], "light")
        self.assertIn("compacted", [ev["type"] for ev in events])
        self.assertEqual(loop.memory.get_summary("c3", db), "摘要内容")

    def test_get_history_outbound_view(self):
        """有摘要时：出站视图只保留最近消息且不含摘要块（摘要由快照消息承载），canonical 历史不动"""
        db = make_db()
        mem = ChatMemory()
        sid = mem.get_or_create_session("c1", db)
        for i in range(15):
            mem.save_message(sid, "user", f"q{i}", db=db)
            mem.save_message(sid, "assistant", f"a{i}", db=db)

        mem.save_summary(sid, "摘要：讨论策略", db)
        history = mem.get_history(sid, db)
        # 摘要不再以 system 消息注入 history（改由 loop 拼入快照）
        self.assertNotIn("[历史对话摘要]", history[0]["content"])
        self.assertEqual(history[0]["role"], "user")
        # 压缩后出站最多 fetch limit*3 = 8*3 = 24 条
        self.assertLessEqual(len(history), 24)
        self.assertLess(len(history), 30)
        # canonical 历史完整保留
        self.assertEqual(len(mem.get_all_messages(sid, db)), 30)

    def test_loop_compaction_events(self):
        db = make_db()
        loop = make_loop(db)
        loop.client.chat.completions.create.return_value = final_resp()
        sid = loop.memory.get_or_create_session("c2", db)
        for i in range(40):
            loop.memory.save_message(sid, "user", "很长的分析问题" * 200, db=db)
            loop.memory.save_message(sid, "assistant", "很长的分析回答" * 200, db=db)

        with patch("app.agent_core.loop.settings") as st:
            st.llm_model = "mock-model"
            st.context_window_tokens = 1000
            st.compaction_threshold = 0.5
            st.context_compaction_light_ratio = 0.3
            st.compaction_min_tokens = 100
            st.llm_stream_retries = 0
            st.llm_stream_retry_base = 1.0
            from app.agent_core import provider as provider_mod
            with patch.object(provider_mod, "stream_completion",
                              side_effect=[iter(text_stream("好"))]):
                events = list(loop._iter_events("你好", "c2", db))

        types = [ev["type"] for ev in events]
        self.assertIn("compacting", types)
        self.assertIn("compacted", types)
        self.assertTrue(loop.memory.get_summary("c2", db))


class TestParallel(unittest.TestCase):
    """P4 - 读工具并行执行、事件顺序稳定"""

    def test_is_parallel_safe(self):
        db = make_db()
        loop = make_loop(db)
        loop.registry.get_tool.side_effect = lambda n: SimpleNamespace(risk_level="read" if n == "r" else "write")
        self.assertTrue(loop._is_parallel_safe("r"))
        self.assertFalse(loop._is_parallel_safe("w"))

    def test_parallel_events_order(self):
        """同轮 3 个读工具：先全部 tool_started，再按序 tool_finished，最终无 tool 回复"""
        db = make_db()
        loop = make_loop(db)
        loop.registry.get_tool.return_value = SimpleNamespace(risk_level="read")
        loop.registry.execute.return_value = {"ok": True}

        tcs = [
            SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=f"read{i}", arguments="{}"))
            for i in range(3)
        ]
        stream = [tool_calls_stream(tcs), text_stream("完成")]
        sid = loop.memory.get_or_create_session("p1", db)
        events = iter_events(loop, "并行读", "p1", db, stream=stream)
        types = [ev["type"] for ev in events]

        self.assertEqual(loop.registry.execute.call_count, 3)
        started = [i for i, t in enumerate(types) if t == "tool_started"]
        finished = [i for i, t in enumerate(types) if t == "tool_finished"]
        # 所有 started 都在第一个 finished 之前
        self.assertLess(max(started), min(finished))
        self.assertEqual(len(finished), 3)
        self.assertEqual(len([t for t in types if t == "assistant_message"]), 1)
        # 顺序稳定：tool_finished 的 seq 递增
        seqs = [ev["data"]["seq"] for ev in events if ev["type"] == "tool_finished"]
        self.assertEqual(seqs, sorted(seqs))


class TestProvider(unittest.TestCase):
    """P5 - 模型前缀路由 + 会话级切模型"""

    def test_parse_aliases_two_forms(self):
        from app.agent_core.provider import parse_aliases

        with patch("app.agent_core.provider.get_settings") as gs:
            gs.return_value.llm_model_aliases = json.dumps({
                "qwen": "https://dashscope/v1",
                "deepseek": {"base_url": "https://ds/v1", "api_key": "sk-x"},
            })
            aliases = parse_aliases()
            self.assertEqual(aliases["qwen"], {"base_url": "https://dashscope/v1", "api_key": None})
            self.assertEqual(aliases["deepseek"]["api_key"], "sk-x")

    def test_parse_aliases_invalid_json(self):
        from app.agent_core.provider import parse_aliases

        with patch("app.agent_core.provider.get_settings") as gs:
            gs.return_value.llm_model_aliases = "{not json"
            self.assertEqual(parse_aliases(), {})

    def test_resolve_llm(self):
        from app.agent_core.provider import resolve_llm

        with patch("app.agent_core.provider.get_settings") as gs:
            st = gs.return_value
            st.llm_model_aliases = json.dumps({
                "qwen": "https://dashscope/v1",
                "qwen-long": "https://dashscope-long/v1",
            })
            st.llm_api_base_url = "https://default/v1"
            st.llm_api_key = "sk-default"
            # 最长前缀优先
            bu, ak, m = resolve_llm("qwen-long-max")
            self.assertEqual(bu, "https://dashscope-long/v1")
            # 裸模型名 → 默认回退
            bu, ak, m = resolve_llm("gpt-4o")
            self.assertEqual(bu, "https://default/v1")
            self.assertEqual(ak, "sk-default")

    def test_session_model_set_get(self):
        db = make_db()
        mem = ChatMemory()
        sid = mem.get_or_create_session("p5", db)
        self.assertEqual(mem.get_session_model("p5", db), "")
        mem.set_session_model("p5", "qwen-max", db)
        self.assertEqual(mem.get_session_model("p5", db), "qwen-max")
        mem.set_session_model("p5", "", db)
        self.assertEqual(mem.get_session_model("p5", db), "")

    def test_loop_uses_session_model(self):
        """会话指定模型时：用解析出的客户端，并以会话模型名调用"""
        db = make_db()
        loop = make_loop(db)
        sid = loop.memory.get_or_create_session("p6", db)
        loop.memory.set_session_model("p6", "qwen-max", db)

        sess_client = MagicMock()
        with patch("app.agent_core.loop.settings") as st, \
             patch("app.agent_core.loop.provider.resolve_llm", return_value=("https://dashscope/v1", "sk", "qwen-max")), \
             patch("app.agent_core.loop.provider.build_openai_client", return_value=sess_client), \
             patch("app.agent_core.provider.stream_completion") as sc:
            st.llm_model = "mock-model"
            st.context_window_tokens = 128000
            st.compaction_threshold = 0.8
            st.context_compaction_light_ratio = 0.55
            st.compaction_min_tokens = 20000
            st.llm_stream_retries = 0
            st.llm_stream_retry_base = 1.0
            sc.return_value = iter(text_stream("好"))
            events = list(loop._iter_events("你好", "p6", db))

        sc.assert_called()
        self.assertEqual(sc.call_args.args[1], "qwen-max")  # (client, model, messages, tools)
        self.assertEqual(sc.call_args.args[0], sess_client)
        self.assertIn("assistant_message", [ev["type"] for ev in events])


class TestInterrupt(unittest.TestCase):
    """P6 - 中断控制"""

    def test_interrupt_flags(self):
        from app.agent_core.loop import request_interrupt, check_interrupted, clear_interrupt

        clear_interrupt("i1")
        self.assertFalse(check_interrupted("i1"))
        request_interrupt("i1")
        self.assertTrue(check_interrupted("i1"))
        # 不同会话互不影响
        self.assertFalse(check_interrupted("i2"))
        clear_interrupt("i1")
        self.assertFalse(check_interrupted("i1"))

    def test_loop_interrupt_fills_error(self):
        """中断在工具执行时置位：已执行工具标 error，循环终止、无最终回复"""
        from app.agent_core.loop import request_interrupt, clear_interrupt

        db = make_db()
        loop = make_loop(db)
        loop.registry.get_tool.return_value = SimpleNamespace(risk_level="read")

        def exec_with_interrupt(*args, **kwargs):
            request_interrupt("i3")
            return {"ok": True}

        loop.registry.execute.side_effect = exec_with_interrupt

        tcs = [SimpleNamespace(id="c1", function=SimpleNamespace(name="read1", arguments="{}"))]
        stream = tool_calls_stream(tcs) + [{"type": "text_delta", "content": "不该出现"}, {"type": "done"}]

        clear_interrupt("i3")
        sid = loop.memory.get_or_create_session("i3", db)
        events = iter_events(loop, "跑起来", "i3", db, stream=stream)
        types = [ev["type"] for ev in events]

        self.assertIn("interrupted", types)
        # 中断后该工具标 error 并终止，不产出最终 assistant_message
        finished = [ev for ev in events if ev["type"] == "tool_finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["data"]["status"], "error")
        self.assertNotIn("不该出现", [ev.get("data", {}).get("content", "") for ev in events])


if __name__ == "__main__":
    unittest.main()
