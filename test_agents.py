"""
Agent 系统测试脚本
用法: source .venv/bin/activate && python test_agents.py
"""
import unittest
from unittest.mock import MagicMock, patch
import json
import tempfile
import os
import sys


class TestModuleImports(unittest.TestCase):
    """测试所有模块可正常导入"""

    def test_01_base_agent(self):
        from app.agents.base import BaseAgent
        self.assertTrue(hasattr(BaseAgent, 'call_llm'))
        self.assertTrue(hasattr(BaseAgent, '_parse_json'))

    def test_02_technical_analyst(self):
        from app.agents.technical_analyst import TechnicalAnalystAgent
        a = TechnicalAnalystAgent()
        self.assertEqual(a.name, "technical_analyst")
        self.assertTrue(hasattr(a, 'analyze'))

    def test_03_sentiment_analyst(self):
        from app.agents.sentiment_analyst import SentimentAnalystAgent
        a = SentimentAnalystAgent()
        self.assertEqual(a.name, "sentiment_analyst")
        self.assertTrue(hasattr(a, 'analyze'))

    def test_04_market_analyst(self):
        from app.agents.market_analyst import MarketAnalystAgent
        a = MarketAnalystAgent()
        self.assertTrue(hasattr(a, 'analyze'))
        # 验证支持辩论参数
        import inspect
        sig = inspect.signature(a.analyze)
        self.assertIn('bull_report', sig.parameters)
        self.assertIn('bear_report', sig.parameters)

    def test_05_bull_researcher(self):
        from app.agents.bull_researcher import BullResearcher
        a = BullResearcher()
        self.assertEqual(a.name, "bull_researcher")

    def test_06_bear_researcher(self):
        from app.agents.bear_researcher import BearResearcher
        a = BearResearcher()
        self.assertEqual(a.name, "bear_researcher")

    def test_07_risk_agents(self):
        from app.agents.risk_agents.aggressive_risk import AggressiveRiskAgent
        from app.agents.risk_agents.conservative_risk import ConservativeRiskAgent
        from app.agents.risk_agents.neutral_risk import NeutralRiskAgent
        from app.agents.risk_agents.risk_manager import RiskManager
        self.assertEqual(AggressiveRiskAgent().name, "aggressive_risk")
        self.assertEqual(ConservativeRiskAgent().name, "conservative_risk")
        self.assertEqual(NeutralRiskAgent().name, "neutral_risk")
        self.assertEqual(RiskManager().name, "risk_manager")

    def test_08_orchestrator(self):
        from app.agents.orchestrator import Orchestrator
        o = Orchestrator()
        self.assertTrue(hasattr(o, 'technical_analyst'))
        self.assertTrue(hasattr(o, 'sentiment_analyst'))
        self.assertTrue(hasattr(o, 'market_analyst'))
        self.assertTrue(hasattr(o, 'bull_researcher'))
        self.assertTrue(hasattr(o, 'bear_researcher'))

    def test_09_risk_debate(self):
        from app.agents.risk_agents.risk_debate_orchestrator import RiskDebateOrchestrator
        r = RiskDebateOrchestrator()
        self.assertTrue(hasattr(r, 'aggressive'))
        self.assertTrue(hasattr(r, 'conservative'))
        self.assertTrue(hasattr(r, 'neutral'))
        self.assertTrue(hasattr(r, 'manager'))

    def test_10_memory_log(self):
        from app.memory.memory_log import MemoryLog
        ml = MemoryLog(999)
        self.assertEqual(ml.strategy_id, 999)
        self.assertTrue(ml.filepath.endswith("strategy_999.md"))


class TestBaseAgentJsonParsing(unittest.TestCase):
    """测试基类 JSON 解析"""

    def setUp(self):
        from app.agents.base import BaseAgent
        self.agent = BaseAgent()

    def test_parse_valid_json(self):
        result = self.agent._parse_json('{"key": "value", "num": 42}')
        self.assertEqual(result["key"], "value")
        self.assertEqual(result["num"], 42)

    def test_parse_json_with_markdown(self):
        content = '''```json
{"market": "bullish", "score": 0.8}
```'''
        result = self.agent._parse_json(content)
        self.assertEqual(result["market"], "bullish")
        self.assertEqual(result["score"], 0.8)

    def test_parse_json_with_text(self):
        content = '以下是分析结果：\n{"action": "hold", "reason": "市场不明"}\n请查收。'
        result = self.agent._parse_json(content)
        self.assertEqual(result["action"], "hold")

    def test_parse_invalid_json(self):
        result = self.agent._parse_json("不是JSON")
        self.assertIn("error", result)

    def test_call_llm_no_client(self):
        self.agent.llm_client = None
        result = self.agent.call_llm("test")
        self.assertIn("error", result)
        self.assertIn("未配置", result["error"])


class TestAgentPromptFormatting(unittest.TestCase):
    """测试 Agent prompt 能正确格式化（mock LLM，不真实调用）"""

    @patch('app.agents.technical_analyst.TechnicalAnalystAgent.call_llm')
    def test_technical_analyst_formats_prompt(self, mock_call):
        from app.agents.technical_analyst import TechnicalAnalystAgent
        mock_call.return_value = {"overall_trend": "bullish", "key_indicators": []}

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        a = TechnicalAnalystAgent()
        a.llm_client = MagicMock()
        result = a.analyze(["510050", "510300"], db)

        self.assertEqual(result["overall_trend"], "bullish")
        mock_call.assert_called_once()
        prompt_arg = mock_call.call_args[0][0]
        self.assertIn("技术指标数据", prompt_arg)
        self.assertIn("510050", prompt_arg)

    def test_sentiment_analyst_formats_prompt(self):
        from app.agents.sentiment_analyst import SentimentAnalystAgent
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        a = SentimentAnalystAgent()
        sentiment_summary = a._get_sentiment_summary("2026-06-11", db)
        self.assertEqual(sentiment_summary["total"], 0)
        self.assertIn("message", sentiment_summary)

    def test_sentiment_analyst_with_data(self):
        from app.agents.sentiment_analyst import SentimentAnalystAgent
        from datetime import date

        mock_news = [
            MagicMock(title="利好新闻", sentiment_label="positive", sentiment_score=0.8, key_factors=["政策"]),
            MagicMock(title="中性新闻", sentiment_label="neutral", sentiment_score=0.0, key_factors=[]),
            MagicMock(title="利空新闻", sentiment_label="negative", sentiment_score=-0.6, key_factors=["风险"]),
        ]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = mock_news

        a = SentimentAnalystAgent()
        result = a._get_sentiment_summary(date(2026, 6, 11), db)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["positive_count"], 1)
        self.assertEqual(result["negative_count"], 1)
        self.assertAlmostEqual(result["avg_sentiment_score"], (0.8 + 0.0 - 0.6) / 3, places=2)

    def test_market_analyst_includes_debate_in_prompt(self):
        from app.agents.market_analyst import MarketAnalystAgent
        self.assertIn("bull_report", MarketAnalystAgent.PROMPT)
        self.assertIn("bear_report", MarketAnalystAgent.PROMPT)
        self.assertIn("多头研究员报告", MarketAnalystAgent.PROMPT)
        self.assertIn("空头研究员报告", MarketAnalystAgent.PROMPT)

    def test_bull_bear_researcher_prompts(self):
        from app.agents.bull_researcher import BullResearcher
        from app.agents.bear_researcher import BearResearcher
        self.assertIn("多头", BullResearcher.PROMPT)
        self.assertIn("看多", BullResearcher.PROMPT)
        self.assertIn("空头", BearResearcher.PROMPT)
        self.assertIn("看空", BearResearcher.PROMPT)

    def test_risk_agent_prompts(self):
        from app.agents.risk_agents.aggressive_risk import AggressiveRiskAgent
        from app.agents.risk_agents.conservative_risk import ConservativeRiskAgent
        from app.agents.risk_agents.neutral_risk import NeutralRiskAgent
        from app.agents.risk_agents.risk_manager import RiskManager
        self.assertIn("激进", AggressiveRiskAgent.PROMPT)
        self.assertIn("保守", ConservativeRiskAgent.PROMPT)
        self.assertIn("中性", NeutralRiskAgent.PROMPT)
        self.assertIn("激进风控官", RiskManager.PROMPT)
        self.assertIn("保守风控官", RiskManager.PROMPT)
        self.assertIn("中性风控官", RiskManager.PROMPT)


class TestOrchestratorWithMocks(unittest.TestCase):
    """测试 Orchestrator 辩论流程（全部 mock LLM）"""

    def setUp(self):
        from app.agents.orchestrator import Orchestrator
        self.orchestrator = Orchestrator()
        # Mock all sub-agents
        self.orchestrator.technical_analyst = MagicMock()
        self.orchestrator.sentiment_analyst = MagicMock()
        self.orchestrator.bull_researcher = MagicMock()
        self.orchestrator.bear_researcher = MagicMock()
        self.orchestrator.market_analyst = MagicMock()

    def test_full_debate_flow(self):
        """验证辩论流程：技术→情绪→多头→空头→主管"""
        from datetime import date

        self.orchestrator.technical_analyst.analyze.return_value = {
            "overall_trend": "bullish", "key_indicators": [], "etf_rankings": []
        }
        self.orchestrator.sentiment_analyst.analyze.return_value = {
            "market_sentiment": "bullish", "sentiment_score": 0.3
        }
        self.orchestrator.bull_researcher.analyze.return_value = {
            "bullish_case": "均线多头排列", "conviction_level": "high"
        }
        self.orchestrator.bear_researcher.analyze.return_value = {
            "bearish_case": "RSI超买", "conviction_level": "medium"
        }
        self.orchestrator.market_analyst.analyze.return_value = {
            "market_regime": "bull_quiet", "suggested_action": "hold",
            "suggested_allocation": {}, "agreement_level": "consensus",
        }

        db = MagicMock()
        strategy_mock = MagicMock()
        strategy_mock.allocation_config = {"510050": 0.5, "510300": 0.5}
        strategy_mock.last_analysis_result = None
        strategy_mock.last_auto_analysis_date = None
        db.query.return_value.filter.return_value.first.return_value = strategy_mock

        result = self.orchestrator.analyze(1, date(2026, 6, 11), db)

        # 验证所有 Agent 都被调用
        self.orchestrator.technical_analyst.analyze.assert_called_once()
        self.orchestrator.sentiment_analyst.analyze.assert_called_once()
        self.orchestrator.bull_researcher.analyze.assert_called_once()
        self.orchestrator.bear_researcher.analyze.assert_called_once()
        self.orchestrator.market_analyst.analyze.assert_called_once()

        # 验证 bull/bear 报告的输入来自 technical + sentiment
        bull_args = self.orchestrator.bull_researcher.analyze.call_args[0]
        self.assertEqual(bull_args[0]["overall_trend"], "bullish")
        self.assertEqual(bull_args[1]["market_sentiment"], "bullish")

        # 验证 final decision 包含所有报告
        self.assertEqual(result["technical_report"]["overall_trend"], "bullish")
        self.assertEqual(result["sentiment_report"]["market_sentiment"], "bullish")
        self.assertEqual(result["bull_report"]["bullish_case"], "均线多头排列")
        self.assertEqual(result["bear_report"]["bearish_case"], "RSI超买")
        self.assertEqual(result["market_regime"], "bull_quiet")

        # 验证数据被持久化
        self.assertEqual(strategy_mock.last_analysis_result, result)
        db.commit.assert_called_once()


class TestRiskDebateWithMocks(unittest.TestCase):
    """测试 RiskDebate 流程（mock LLM）"""

    def setUp(self):
        from app.agents.risk_agents.risk_debate_orchestrator import RiskDebateOrchestrator
        self.debate = RiskDebateOrchestrator()
        self.debate.aggressive = MagicMock()
        self.debate.conservative = MagicMock()
        self.debate.neutral = MagicMock()
        self.debate.manager = MagicMock()

    def test_normal_flow(self):
        self.debate.aggressive.analyze.return_value = {
            "risk_philosophy": "aggressive", "risk_level": "low", "position_adjustment": 1.0
        }
        self.debate.conservative.analyze.return_value = {
            "risk_philosophy": "conservative", "risk_level": "medium", "position_adjustment": 0.7
        }
        self.debate.neutral.analyze.return_value = {
            "risk_philosophy": "neutral", "risk_level": "low", "position_adjustment": 0.9
        }
        self.debate.manager.analyze.return_value = {
            "final_risk_level": "low", "final_suggested_action": "proceed",
            "final_position_adjustment": 0.9, "agreement": "partial",
            "adopted_philosophy": "compromise",
        }

        db = MagicMock()
        ctrl = MagicMock()
        ctrl.check_circuit_breaker.return_value = {"status": "normal"}
        ctrl.apply_drawdown_protection.return_value = {"status": "normal", "drawdown_pct": -2.0}
        ctrl.check_risk_budget.return_value = {"status": "compliant"}
        ctrl.run_stress_test.return_value = {"status": "completed", "scenarios": [], "worst_case": {}}

        with patch('app.agents.risk_agents.risk_debate_orchestrator.RiskController', return_value=ctrl):
            result = self.debate.evaluate(1, "bull_quiet", db)

        self.assertEqual(result["stage"], "risk_check")
        self.assertEqual(result["status"], "passed")

    def test_circuit_breaker_bypasses_debate(self):
        db = MagicMock()
        ctrl = MagicMock()
        ctrl.check_circuit_breaker.return_value = {
            "status": "triggered", "reason": "连续3次亏损", "action": "pause_strategy"
        }
        ctrl.apply_drawdown_protection.return_value = {"status": "normal"}

        with patch('app.agents.risk_agents.risk_debate_orchestrator.RiskController', return_value=ctrl):
            result = self.debate.evaluate(1, "bear_panic", db)

        self.assertEqual(result["status"], "triggered")
        # 熔断时不应调用 LLM
        self.debate.aggressive.analyze.assert_not_called()

    def test_critical_drawdown_bypasses_debate(self):
        db = MagicMock()
        ctrl = MagicMock()
        ctrl.check_circuit_breaker.return_value = {"status": "normal"}
        ctrl.apply_drawdown_protection.return_value = {
            "status": "critical", "message": "回撤15%", "suggested_allocation": {"510050": 0.25}
        }

        with patch('app.agents.risk_agents.risk_debate_orchestrator.RiskController', return_value=ctrl):
            result = self.debate.evaluate(1, "bear_panic", db)

        self.assertEqual(result["status"], "critical")
        self.assertIn("回撤", result["reason"])
        self.debate.aggressive.analyze.assert_not_called()


class TestMemoryLog(unittest.TestCase):
    """测试 MemoryLog 文件写入"""

    def setUp(self):
        from app.memory.memory_log import MEMORY_DIR
        self.test_dir = tempfile.mkdtemp()
        self._orig_dir = MEMORY_DIR

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch('app.memory.memory_log.MEMORY_DIR', new_callable=lambda: tempfile.mkdtemp())
    def test_record_decision(self, mock_dir):
        from app.memory.memory_log import MemoryLog
        ml = MemoryLog(777)
        decision = {
            "analysis_date": "2026-06-11",
            "market_regime": "bull_quiet",
            "market_sentiment": "bullish",
            "suggested_action": "hold",
            "confidence_level": "high",
            "action_reason": "市场趋势良好",
            "technical_report": {"overall_trend": "bullish", "summary": "看多"},
            "sentiment_report": {"market_sentiment": "bullish", "summary": "乐观"},
            "suggested_allocation": {"510050": 0.5, "510300": 0.5},
            "risk_alert": {"level": "low", "factors": []},
            "agreement_level": "consensus",
            "agreement_note": "无分歧",
        }
        ml.record_decision(decision)

        self.assertTrue(os.path.exists(ml.filepath))
        content = open(ml.filepath, encoding="utf-8").read()
        self.assertIn("决策记录", content)
        self.assertIn("bull_quiet", content)
        self.assertIn("技术分析师观点", content)
        self.assertIn("情绪分析师观点", content)
        self.assertIn("510050", content)

    @patch('app.memory.memory_log.MEMORY_DIR', new_callable=lambda: tempfile.mkdtemp())
    def test_record_outcome(self, mock_dir):
        from app.memory.memory_log import MemoryLog
        ml = MemoryLog(777)
        ml.record_outcome("2026-06-11", 2.5, "盈利")
        content = open(ml.filepath, encoding="utf-8").read()
        self.assertIn("结果反馈", content)
        self.assertIn("2.50%", content)
        self.assertIn("盈利", content)


class TestExecutorIntegration(unittest.TestCase):
    """测试 Executor 中新的导入路径"""

    def test_analysis_uses_orchestrator(self):
        """验证 _run_analysis 导入了 Orchestrator 而非 AutoAnalysisService"""
        import importlib
        import app.services.auto_strategy_executor
        source = open(importlib.util.find_spec("app.services.auto_strategy_executor").origin).read()
        self.assertIn("from app.agents.orchestrator import Orchestrator", source)
        self.assertNotIn("from app.services.auto_analysis_service import AutoAnalysisService", source)

    def test_risk_uses_debate(self):
        """验证 _run_risk_checks 导入了 RiskDebateOrchestrator"""
        import importlib
        import app.services.auto_strategy_executor
        source = open(importlib.util.find_spec("app.services.auto_strategy_executor").origin).read()
        self.assertIn("from app.agents.risk_agents.risk_debate_orchestrator import RiskDebateOrchestrator", source)


if __name__ == "__main__":
    # 确保项目目录在 path 中
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    unittest.main(verbosity=2)
