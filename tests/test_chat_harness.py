"""侧边栏 AI 助手 harness 测试（方案：plans/侧边栏AI助手Harness能力优化方案.md）"""

import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.agent_core.memory import ChatMemory, _rebuild_history


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


if __name__ == "__main__":
    unittest.main()
