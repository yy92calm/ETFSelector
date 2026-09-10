"""个股数据仅限板块辅助改造测试：宏观Agent行业排名注入/降级、舆情prompt约束、前端分组数据完整性"""
import unittest
from datetime import date
from unittest.mock import patch

from tests.test_value_model import make_db


def seed_industry(db, day, industry, score, rank):
    from app.models.stock_fundamental import IndustryScore
    db.add(IndustryScore(trade_date=day, industry=industry, score=score,
                         rank=rank, sample_count=10))
    db.commit()


class TestMacroIndustryRanking(unittest.TestCase):
    """MacroCycleAgent._build_industry_ranking"""

    def setUp(self):
        self.agent = __import__(
            "app.agents.macro_cycle_agent", fromlist=["MacroCycleAgent"]
        ).MacroCycleAgent()
        self.db = make_db()

    def test_empty_db_degrades(self):
        """无评分数据时降级为占位说明，不抛异常"""
        self.assertEqual(self.agent._build_industry_ranking(self.db), "暂无行业评分数据")

    def test_ranking_injected(self):
        day = date(2026, 9, 10)
        for i in range(12):
            seed_industry(self.db, day, f"行业{i}", 90.0 - i * 5, i + 1)
        text = self.agent._build_industry_ranking(self.db)
        self.assertIn(str(day), text)
        self.assertIn("第1名 行业0", text)
        self.assertIn("第12名 行业11", text)  # 尾部5名也包含

    def test_service_error_degrades(self):
        """查询异常时降级而非抛出"""
        with patch("app.services.value_model_service.get_value_model_service",
                   side_effect=RuntimeError("boom")):
            self.assertEqual(
                self.agent._build_industry_ranking(self.db), "暂无行业评分数据"
            )

    def test_prompt_formats_without_error(self):
        """PROMPT 含新占位符且 format 不抛异常"""
        prompt = self.agent.PROMPT.format(market_data="M", industry_ranking="R")
        self.assertIn("R", prompt)
        self.assertIn("不得据此输出个股标的", prompt)


class TestSentimentPromptConstraints(unittest.TestCase):
    """舆情链路两处 prompt 的个股用途约束"""

    def test_sentiment_service_prompt_constraint(self):
        from app.services.sentiment_service import SentimentService
        self.assertIn("不得将其本身作为投资标的判断依据",
                      SentimentService.SENTIMENT_ANALYSIS_PROMPT)

    def test_sentiment_analyst_prompt_constraint(self):
        from app.agents.sentiment_analyst import SentimentAnalystAgent
        self.assertIn("不得推荐个股", SentimentAnalystAgent.PROMPT)
        # format 占位符仍完整
        SentimentAnalystAgent.PROMPT.format(sentiment_index="a", sentiment_summary="b")


class TestStockPicksGroupingData(unittest.TestCase):
    """前端按行业分组依赖 industry 与 score 字段非空可用"""

    def test_picks_carry_industry_and_score(self):
        from app.services.value_model_service import ValueModelService
        db = make_db()
        from tests.test_value_model import seed_stock
        end = date(2026, 9, 9)
        for i in range(6):
            seed_stock(db, f"sh.6003{i:02d}", f"芯股{i}", "计算机通信",
                       end, pe_series=15.0, ni_yoy=40.0)
        picks = ValueModelService().get_stock_picks(db)
        self.assertTrue(picks)
        for p in picks:
            self.assertIn("industry", p)
            self.assertIsNotNone(p["industry"])
            self.assertIsInstance(p["score"], float)


class TestTriggerReviewTool(unittest.TestCase):
    """AI助手 trigger_review 工具注册与入参校验"""

    def test_registered_as_write_tool(self):
        from app.tools.registry import get_tool_registry
        t = get_tool_registry().get_tool("trigger_review")
        self.assertIsNotNone(t)
        self.assertEqual(t.risk_level, "write")

    def test_invalid_review_type(self):
        from app.tools.analysis_tools import trigger_review
        db = make_db()
        result = trigger_review(db=db, strategy_id=1, review_type="daily")
        self.assertIn("error", result)

    def test_missing_strategy(self):
        from app.tools.analysis_tools import trigger_review
        db = make_db()
        result = trigger_review(db=db, strategy_id=999, review_type="weekly")
        self.assertIn("error", result)


class TestAssistantToolExpansion(unittest.TestCase):
    """AI助手扩具清单：注册完整性与风险归类"""

    READ_TOOLS = [
        "get_review_report", "get_execution_logs", "get_drawdown_attribution",
        "find_similar_environments", "smart_match_experiences",
        "get_industry_ranking", "get_market_regime", "get_banned_codes",
        "get_pipeline_status",
    ]
    WRITE_TOOLS = [
        "trigger_daily_pipeline", "trigger_sentiment_collect",
        "catch_up_strategy", "fetch_etf_history",
    ]

    def test_registration_and_risk_level(self):
        from app.tools.registry import get_tool_registry
        reg = get_tool_registry()
        for name in self.READ_TOOLS:
            t = reg.get_tool(name)
            self.assertIsNotNone(t, f"{name} 未注册")
            self.assertEqual(t.risk_level, "read", name)
        for name in self.WRITE_TOOLS:
            t = reg.get_tool(name)
            self.assertIsNotNone(t, f"{name} 未注册")
            self.assertEqual(t.risk_level, "write", name)

    def test_pipeline_status_empty_db(self):
        from app.tools.ops_tools import get_pipeline_status
        db = make_db()
        result = get_pipeline_status(db)
        self.assertEqual(result["status"], "not_started")
        self.assertEqual(len(result["stages"]), 10)

    def test_review_report_invalid_type(self):
        from app.tools.analysis_tools import get_review_report
        db = make_db()
        self.assertIn("error", get_review_report(db, 1, review_type="daily"))

    def test_catch_up_missing_strategy(self):
        from app.tools.ops_tools import catch_up_strategy
        db = make_db()
        self.assertIn("error", catch_up_strategy(db, 999))

    def test_trigger_pipeline_missing_strategy(self):
        from app.tools.ops_tools import trigger_daily_pipeline
        db = make_db()
        self.assertIn("error", trigger_daily_pipeline(db, 999))


if __name__ == "__main__":
    unittest.main()
