"""AgentCore 优化项测试（对齐 deepseek-harness 模式：上下文分离/usage/重试/MCP/技能热更新）

覆盖 plans/AgentCore上下文流式MCP技能优化方案.md 的 P0-P2 优化项。
"""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.agent_core.context import ContextBuilder
from app.agent_core.loop import AgentLoop
from app.agent_core.memory import ChatMemory
from app.agent_core.mcp_bridge import (
    McpBridge,
    _ServerRuntime,
    legacy_tool_name,
    public_tool_name,
)
from app.agent_core.skill_manager import SkillManager
from app.agent_core import provider as provider_mod


def make_db():
    """构造内存 SQLite + 全表 schema"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_loop(db):
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


# ---- provider 流式 mock 工具 ----

def delta_chunk(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content=content, tool_calls=tool_calls),
        finish_reason=None,
    )], usage=None)


def finish_chunk(reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(delta=None, finish_reason=reason)], usage=None)


def usage_tail_chunk(usage):
    return SimpleNamespace(choices=[], usage=usage)


def make_stream_client(responses):
    """create 按次返回 responses 列表中的可迭代 chunk 序列"""
    client = MagicMock()
    client.chat.completions.create.side_effect = [iter(r) for r in responses]
    return client


class TestProviderThinkingDone(unittest.TestCase):
    """P0-3 - 流未输出</think>时，收尾兜底发出 thinking_done"""

    def test_thinking_done_emitted_on_stream_end(self):
        chunks = [delta_chunk("<think>分析中"), delta_chunk("继续思考"), finish_chunk(), usage_tail_chunk(None)]
        client = make_stream_client([chunks])
        events = list(provider_mod.stream_completion(client, "m", []))
        types = [e["type"] for e in events]
        self.assertIn("thinking_delta", types)
        self.assertIn("thinking_done", types)
        self.assertEqual(types[-1], "done")

    def test_thinking_done_not_duplicated_when_closed(self):
        chunks = [delta_chunk("<think>思考"), delta_chunk("</think>正文"), finish_chunk()]
        client = make_stream_client([chunks])
        events = list(provider_mod.stream_completion(client, "m", []))
        dones = [e for e in events if e["type"] == "thinking_done"]
        self.assertEqual(len(dones), 1)


class TestProviderUsage(unittest.TestCase):
    """P1-1 - usage/finish_reason 在 done 前必达"""

    def test_usage_event_with_tail_chunk(self):
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120)
        chunks = [delta_chunk("你好"), finish_chunk(), usage_tail_chunk(usage)]
        client = make_stream_client([chunks])
        events = list(provider_mod.stream_completion(client, "m", []))
        usage_events = [e for e in events if e["type"] == "usage"]
        self.assertEqual(len(usage_events), 1)
        self.assertEqual(usage_events[0]["usage"]["total_tokens"], 120)
        self.assertEqual(usage_events[0]["finish_reason"], "stop")
        # usage 在 done 之前
        self.assertLess(events.index(usage_events[0]), len(events) - 1)

    def test_usage_event_always_emitted_even_without_provider_usage(self):
        chunks = [delta_chunk("hi"), finish_chunk()]
        client = make_stream_client([chunks])
        events = list(provider_mod.stream_completion(client, "m", []))
        self.assertEqual([e["type"] for e in events if e["type"] == "usage"], ["usage"])

    def test_stream_options_400_fallback(self):
        """兼容端拒绝 stream_options（400）时降级重发"""

        class _BadRequest(Exception):
            status_code = 400

        ok_chunks = [delta_chunk("ok"), finish_chunk()]
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if kwargs.get("stream_options"):
                raise _BadRequest("unknown field: stream_options")
            return iter(ok_chunks)

        client = MagicMock()
        client.chat.completions.create.side_effect = create
        events = list(provider_mod.stream_completion(client, "m", []))
        types = [e["type"] for e in events]
        self.assertIn("text_delta", types)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("stream_options", calls[1])


class TestContextSnapshot(unittest.TestCase):
    """P0-1/P2-3 - 快照消息注入、system prompt 稳定、时间头"""

    def test_snapshot_message_injected_before_user(self):
        db = make_db()
        loop = make_loop(db)
        with patch("app.agent_core.loop.settings") as st, \
             patch("app.agent_core.provider.stream_completion") as sc:
            st.llm_model = "m"
            st.context_window_tokens = 128000
            st.compaction_threshold = 0.8
            st.compaction_min_tokens = 20000
            st.llm_stream_retries = 0
            st.llm_stream_retry_base = 1.0
            sc.return_value = iter([{"type": "text_delta", "content": "好"}, {"type": "done"}])
            list(loop._iter_events("问题", None, db))
        msgs = sc.call_args.args[2]
        # system 首位且不含快照动态内容；快照 user 消息紧邻当前用户消息之前
        self.assertEqual(msgs[0]["role"], "system")
        self.assertNotIn("当前时间:", msgs[0]["content"])
        self.assertNotIn("状态快照正文", msgs[0]["content"])
        snapshot = msgs[-2]
        self.assertEqual(snapshot["role"], "user")
        self.assertTrue(snapshot["content"].startswith("[系统状态快照]"))
        self.assertEqual(msgs[-1]["content"], "问题")

    def test_system_prompt_stable_across_rounds(self):
        """两轮请求的 system prompt 字节一致（KV cache 前提）"""
        db = make_db()
        loop = make_loop(db)
        prompts = []
        with patch("app.agent_core.loop.settings") as st, \
             patch("app.agent_core.provider.stream_completion") as sc:
            st.llm_model = "m"
            st.context_window_tokens = 128000
            st.compaction_threshold = 0.8
            st.compaction_min_tokens = 20000
            st.llm_stream_retries = 0
            st.llm_stream_retry_base = 1.0
            sc.return_value = iter([{"type": "text_delta", "content": "好"}, {"type": "done"}])
            for q in ("第一问", "第二问"):
                list(loop._iter_events(q, None, db))
                prompts.append(sc.call_args.args[2][0]["content"])
        self.assertEqual(prompts[0], prompts[1])

    def test_turn_snapshot_contains_time_header_and_tolerates_section_failure(self):
        db = make_db()
        builder = ContextBuilder()
        with patch.object(builder, "_get_market_summary", side_effect=RuntimeError("db down")):
            snapshot = builder.build_turn_snapshot(db)
        self.assertIn("当前时间:", snapshot)
        self.assertIn("北京时间", snapshot)
        # 单 section 失败不拖垮整体（快照仍生成）
        self.assertTrue(snapshot)

    def test_empty_db_snapshot_fallback(self):
        db = make_db()
        snapshot = ContextBuilder().build_turn_snapshot(db)
        self.assertIn("当前时间:", snapshot)
        self.assertIn("系统刚初始化", snapshot)


class TestLoopRetry(unittest.TestCase):
    """P1-2 - 流中瞬态错误有界重试"""

    def _run(self, side_effects, retries):
        db = make_db()
        loop = make_loop(db)
        with patch("app.agent_core.loop.settings") as st, \
             patch("app.agent_core.provider.stream_completion") as sc, \
             patch("app.agent_core.loop.time.sleep") as sleeper:
            st.llm_model = "m"
            st.context_window_tokens = 128000
            st.compaction_threshold = 0.8
            st.compaction_min_tokens = 20000
            st.llm_stream_retries = retries
            st.llm_stream_retry_base = 0.01
            sc.side_effect = side_effects
            events = list(loop._iter_events("问", None, db))
        return events, sc.call_count, sleeper.call_count

    def test_retry_then_success(self):
        events, calls, sleeps = self._run(
            [iter([{"type": "error", "error": "transient"}]), iter([{"type": "text_delta", "content": "好"}, {"type": "done"}])],
            retries=2,
        )
        self.assertEqual(calls, 2)
        self.assertEqual(sleeps, 1)
        self.assertIn("assistant_message", [e["type"] for e in events])

    def test_no_retry_after_content_emitted(self):
        """已推送增量后出错不重试（避免前端收到重复内容）"""
        events, calls, sleeps = self._run(
            [iter([{"type": "text_delta", "content": "半截"}, {"type": "error", "error": "boom"}])],
            retries=2,
        )
        self.assertEqual(calls, 1)
        self.assertEqual(sleeps, 0)
        self.assertIn("error", [e["type"] for e in events])

    def test_gives_up_after_max_retries(self):
        events, calls, sleeps = self._run(
            [iter([{"type": "error", "error": "down"}]) for _ in range(3)],
            retries=2,
        )
        self.assertEqual(calls, 3)
        self.assertEqual(sleeps, 2)
        self.assertIn("error", [e["type"] for e in events])


class TestMcpNaming(unittest.TestCase):
    """P0-2 - MCP 工具命名对齐 mcp__<server>__<raw>，非法 server 名跳过"""

    def test_public_and_legacy_names(self):
        self.assertEqual(public_tool_name("web", "search"), "mcp__web__search")
        self.assertEqual(legacy_tool_name("web", "search"), "web.search")

    def test_invalid_server_name_skipped(self):
        bridge = McpBridge.__new__(McpBridge)
        config = '[{"name":"bad.name","type":"http","url":"http://x"},{"name":"ok-name","type":"http","url":"http://y"}]'
        servers = bridge._parse_servers(config)
        self.assertEqual([s["name"] for s in servers], ["ok-name"])

    def test_runtime_backoff_and_disable(self):
        rt = _ServerRuntime({"name": "s", "type": "http", "url": "x"})
        self.assertFalse(rt.disabled)
        for _ in range(9):
            rt.record_failure()
        self.assertFalse(rt.disabled)
        self.assertTrue(rt.in_backoff())
        rt.record_failure()  # 第 10 次
        self.assertTrue(rt.disabled)


class TestMcpAsyncSafety(unittest.TestCase):
    """P1-3 - 运行中事件循环内协程提交后台 loop，不抛 RuntimeError"""

    def test_run_coroutine_inside_running_loop(self):
        async def coro():
            await asyncio.sleep(0)
            return 42

        async def caller():
            # 运行中的 loop 内直接调用同步包装
            return McpBridge._run_coroutine(coro())

        self.assertEqual(asyncio.run(caller()), 42)

    def test_call_tool_unknown_server(self):
        bridge = McpBridge.__new__(McpBridge)
        bridge.servers = []
        result = bridge.call_tool("ghost", "t", {})
        self.assertIn("error", result)


class TestSkillHotReload(unittest.TestCase):
    """P1-4/P2-2 - 技能热更新、调用策略、多根目录优先级"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "alpha.md").write_text(
            "---\nname: alpha\ndescription: 技能A\n---\n正文A", encoding="utf-8"
        )
        self.manager = SkillManager(skills_dir=self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hot_reload_picks_new_file(self):
        self.assertNotIn("beta", self.manager.get_skill_names())
        # 触碰目录 mtime（写入新文件自然更新）
        time.sleep(0.01)
        (self.dir / "beta.md").write_text(
            "---\nname: beta\ndescription: 技能B\nmodel_invocable: false\n---\n正文B",
            encoding="utf-8",
        )
        self.assertIn("beta", self.manager.get_skill_names())
        # 调用策略：beta 对模型不可见，对用户可见
        model_names = [s["name"] for s in self.manager.list_skills(audience="model")]
        user_names = [s["name"] for s in self.manager.list_skills(audience="user")]
        self.assertNotIn("beta", model_names)
        self.assertIn("beta", user_names)

    def test_extra_dir_lower_priority(self):
        """同名技能：项目目录（rank 100）胜出配置追加目录（rank 300）"""
        extra_tmp = tempfile.TemporaryDirectory()
        extra = Path(extra_tmp.name)
        try:
            (extra / "alpha.md").write_text(
                "---\nname: alpha\ndescription: 追加目录版本\n---\nx", encoding="utf-8"
            )
            with patch("app.agent_core.skill_manager.get_settings") as gs:
                gs.return_value.skill_extra_dirs = f'["{extra}"]'
                names = [s["description"] for s in self.manager.list_skills() if s["name"] == "alpha"]
            self.assertEqual(names, ["技能A"])
        finally:
            extra_tmp.cleanup()

    def test_load_skill_after_reload(self):
        time.sleep(0.01)
        (self.dir / "gamma.md").write_text("---\nname: gamma\ndescription: G\n---\nG正文", encoding="utf-8")
        self.assertEqual(self.manager.load_skill("gamma"), "G正文")


if __name__ == "__main__":
    unittest.main()
