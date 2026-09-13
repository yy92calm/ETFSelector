"""自主决策 analyzed 日志回写测试：结构化映射/无辩论降级/当日重跑去重"""
import unittest
from datetime import date

from tests.test_value_model import make_db
from app.agent_core.loop import AgentResponse
from app.tasks.scheduler import _record_autonomous_analysis


def seed_running_strategy(db, sid=1, status="running"):
    from app.models.strategy import Strategy
    db.add(Strategy(
        id=sid, name=f"自动策略{sid}", strategy_type="auto",
        strategy_source="auto_generated", auto_strategy_status=status,
        status="active", allocation_config={"510300": 1.0},
        rebalance_freq="daily", initial_capital=1000000.0,
    ))
    db.commit()


def make_result(tool_calls=None, content="维持现状，无需调整"):
    return AgentResponse(content=content, tool_calls_made=tool_calls or [])


DEBATE_RESULT = {
    "market_regime": "bull",
    "confidence_level": 0.78,
    "suggested_action": "adjust",
    "suggested_allocation": {"512010": 0.4, "510300": 0.6},
    "action_reason": "牛市加仓医药",
    "risk_alert": {"level": "low", "factors": []},
    "agreement_level": "high",
}


class TestRecordAutonomousAnalysis(unittest.TestCase):

    def setUp(self):
        self.db = make_db()

    def tearDown(self):
        self.db.close()

    def _fetch(self):
        from app.models.auto_strategy_log import AutoStrategyLog
        return self.db.query(AutoStrategyLog).filter_by(
            action_type="analyzed").order_by(AutoStrategyLog.id).all()

    def test_no_running_strategy_skips(self):
        seed_running_strategy(self.db, status="paused")
        _record_autonomous_analysis(make_result(), self.db)
        self.assertEqual(self._fetch(), [])

    def test_minimal_record_without_debate(self):
        """未调用辩论工具时降级：hold + 决策摘要作理由"""
        seed_running_strategy(self.db)
        _record_autonomous_analysis(make_result(content="市场平稳，维持现状"), self.db)
        logs = self._fetch()
        self.assertEqual(len(logs), 1)
        ar = logs[0].analysis_result
        self.assertEqual(ar["suggested_action"], "hold")
        self.assertIn("维持现状", ar["action_reason"])
        self.assertEqual(ar["source"], "agentloop_autonomous")
        self.assertEqual(logs[0].log_date, date.today())

    def test_debate_result_mapped_per_strategy(self):
        """辩论工具结果按 strategy_id 归集映射为结构化字段"""
        seed_running_strategy(self.db, sid=1)
        seed_running_strategy(self.db, sid=2)
        tool_calls = [{
            "tool": "run_multi_agent_analysis",
            "arguments": {"strategy_id": 1},
            "result": DEBATE_RESULT,
        }]
        _record_autonomous_analysis(make_result(tool_calls=tool_calls), self.db)
        logs = {l.strategy_id: l for l in self._fetch()}
        self.assertEqual(set(logs.keys()), {1, 2})
        ar1 = logs[1].analysis_result
        self.assertEqual(ar1["market_regime"], "bull")
        self.assertEqual(ar1["suggested_action"], "adjust")
        self.assertEqual(ar1["suggested_allocation"], {"512010": 0.4, "510300": 0.6})
        self.assertEqual(ar1["regime_confidence"], 0.78)
        # 策略2无辩论结果 → 走降级字段
        ar2 = logs[2].analysis_result
        self.assertEqual(ar2["suggested_action"], "hold")
        self.assertIsNone(ar2["market_regime"])

    def test_error_debate_result_degrades(self):
        """辩论返回 error 时不采用，降级为摘要"""
        seed_running_strategy(self.db)
        tool_calls = [{
            "tool": "run_multi_agent_analysis",
            "arguments": {"strategy_id": 1},
            "result": {"error": "数据不足"},
        }]
        _record_autonomous_analysis(make_result(tool_calls=tool_calls), self.db)
        ar = self._fetch()[0].analysis_result
        self.assertEqual(ar["suggested_action"], "hold")

    def test_same_day_rerun_updates_not_duplicates(self):
        """当日重跑更新已有记录，不重复插入"""
        seed_running_strategy(self.db)
        _record_autonomous_analysis(make_result(content="第一次"), self.db)
        _record_autonomous_analysis(
            make_result(tool_calls=[{
                "tool": "run_multi_agent_analysis",
                "arguments": {"strategy_id": 1},
                "result": DEBATE_RESULT,
            }]),
            self.db,
        )
        logs = self._fetch()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].analysis_result["suggested_action"], "adjust")


if __name__ == "__main__":
    unittest.main()
