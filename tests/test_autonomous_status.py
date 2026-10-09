"""自主决策状态判定 + 历史消息排除：不靠回复文本前缀，也不靠内容比较"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_agent_core import make_db, make_loop
from app.agent_core.memory import ChatMemory


def resp(content, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls or []))])


def tool_resp():
    call = SimpleNamespace(id="call_1",
                           function=SimpleNamespace(name="noop", arguments="{}"))
    return resp("", tool_calls=[call])


class TestAutonomousStatus(unittest.TestCase):
    """AIActionLog.status 必须按「是否拿到结论」判定"""

    def setUp(self):
        self.db = make_db()
        self.loop = make_loop(self.db)
        self.loop._build_skills_summary = lambda: "无"
        self.loop.registry.execute.return_value = {"ok": True}

    def tearDown(self):
        self.db.close()

    def _last_status(self):
        from app.models.chat import AIActionLog
        log = self.db.query(AIActionLog).order_by(AIActionLog.id.desc()).first()
        return log.status if log else None

    def test_reply_starting_with_LLM_is_completed(self):
        """正常结论恰好以 LLM 开头时不能被误判为失败"""
        self.loop.client.chat.completions.create.return_value = resp("LLM 判断维持现状")

        result = self.loop.run_autonomous("daily", "请决策", self.db)

        self.assertEqual(self._last_status(), "completed")
        self.assertEqual(result.content, "LLM 判断维持现状")

    def test_llm_exception_is_failed(self):
        self.loop.client.chat.completions.create.side_effect = RuntimeError("服务不可用")

        result = self.loop.run_autonomous("daily", "请决策", self.db)

        self.assertEqual(self._last_status(), "failed")
        self.assertIsNotNone(result.error)

    def test_exhausted_rounds_without_conclusion_is_failed(self):
        """每轮都只发工具调用、跑满轮数仍无结论 → 不能记为 completed"""
        self.loop.client.chat.completions.create.return_value = tool_resp()

        with patch("app.agent_core.loop.MAX_TOOL_ROUNDS", 3):
            result = self.loop.run_autonomous("daily", "请决策", self.db)

        self.assertEqual(self._last_status(), "failed")
        self.assertEqual(result.content, "")


class TestHistoryExclusion(unittest.TestCase):
    """当前消息按主键排除，与内容是否重复无关"""

    def setUp(self):
        self.db = make_db()
        self.mem = ChatMemory()
        self.sid = self.mem.get_or_create_session("s1", self.db)

    def tearDown(self):
        self.db.close()

    def test_save_message_returns_id(self):
        msg_id = self.mem.save_message(self.sid, "user", "你好", db=self.db)

        self.assertIsInstance(msg_id, int)
        self.assertGreater(msg_id, 0)

    def test_only_the_current_message_is_excluded(self):
        first = self.mem.save_message(self.sid, "user", "维持现状的理由?", db=self.db)
        answer = self.mem.save_message(self.sid, "assistant", "两派分歧不足", db=self.db)
        repeat = self.mem.save_message(self.sid, "user", "维持现状的理由?", db=self.db)

        history = self.mem.get_history(self.sid, self.db, exclude_message_id=repeat)

        self.assertEqual([m["content"] for m in history],
                         ["维持现状的理由?", "两派分歧不足"])
        self.assertEqual(len(self.mem.get_all_messages(self.sid, self.db)), 3)
        self.assertEqual(self.mem.get_history(self.sid, self.db,
                                              exclude_message_id=first)[0]["role"],
                         "assistant")

    def test_no_exclusion_keeps_everything(self):
        self.mem.save_message(self.sid, "user", "问题", db=self.db)
        self.mem.save_message(self.sid, "assistant", "回答", db=self.db)

        history = self.mem.get_history(self.sid, self.db)

        self.assertEqual([m["content"] for m in history], ["问题", "回答"])


if __name__ == "__main__":
    unittest.main()
