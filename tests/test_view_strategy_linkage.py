"""视图策略打通测试：策略标的集合、行情/舆情/研究接口的策略标注"""
import unittest
from datetime import date, datetime

from tests.test_value_model import make_db


DAY = date(2026, 9, 21)


def seed_strategy(db, pool=None, pending=None, capital=100000):
    from app.models.strategy import Strategy
    s = Strategy(name="视图打通策略", allocation_config=pool or {},
                 initial_capital=capital, strategy_type="auto", status="active")
    if pending:
        s.pending_allocation = pending
        s.pending_set_date = date(2026, 9, 18)
    db.add(s)
    db.commit()
    return s


def seed_holding(db, sid, code, qty=100, price=1.0, mv=100.0):
    from app.models.portfolio import Holding
    db.add(Holding(strategy_id=sid, etf_code=code, quantity=qty,
                   avg_cost=price, current_price=price, market_value=mv))
    db.commit()


def seed_etf(db, code, name, day=DAY, score=80.0):
    from app.models.etf import ETFBasic, ETFQuotation, ETFDailyIndicator
    db.add(ETFBasic(etf_code=code, etf_name=name))
    db.add(ETFQuotation(etf_code=code, trade_date=day, close_price=1.0,
                        change_pct=0.5, amount=1e8))
    db.add(ETFDailyIndicator(etf_code=code, trade_date=day, composite_score=score,
                             rank_in_market=1, momentum_5d=1.0, momentum_20d=2.0,
                             trend_strength=3, volatility_20d=10.0, vol_ratio=1.0,
                             ma5=1.0, ma10=1.0, ma20=1.0))
    db.commit()


class TestStrategyUniverse(unittest.TestCase):
    """PortfolioService.get_strategy_universe"""

    def test_pool_includes_pending_and_holdings(self):
        from app.services.portfolio_service import PortfolioService
        db = make_db()
        s = seed_strategy(db, pool={"510300": 0.6}, pending={"511010": 0.4})
        seed_holding(db, s.id, "510300", mv=6000.0)
        u = PortfolioService().get_strategy_universe(s.id, db)
        self.assertEqual(u["pool"], {"510300", "511010"})
        self.assertEqual(u["holding_codes"], {"510300"})
        self.assertEqual(u["total_asset"], 6000.0)
        self.assertTrue(u["exists"])

    def test_missing_strategy_degrades(self):
        from app.services.portfolio_service import PortfolioService
        db = make_db()
        u = PortfolioService().get_strategy_universe(999, db)
        self.assertFalse(u["exists"])
        self.assertEqual(u["pool"], set())
        self.assertEqual(u["total_asset"], 0.0)


class TestMarketIndicatorsStrategyMarks(unittest.TestCase):
    """GET /api/workbench/market-indicators 的策略标注与过滤"""

    def setUp(self):
        from app.routes.workbench_routes import get_market_indicators
        self.route = get_market_indicators
        self.db = make_db()
        self.s = seed_strategy(self.db, pool={"510300": 0.7})
        seed_holding(self.db, self.s.id, "510300", mv=7000.0)
        seed_etf(self.db, "510300", "沪深300ETF", score=90.0)
        seed_etf(self.db, "159915", "创业板ETF", score=80.0)
        seed_etf(self.db, "511010", "国债ETF", score=60.0)

    def test_marks_with_strategy(self):
        resp = self.route(date=DAY.isoformat(), strategy_id=self.s.id, db=self.db)
        rows = {r["etf_code"]: r for r in resp.data["rows"]}
        holding = rows["510300"]["strategy"]
        self.assertTrue(holding["is_holding"])
        self.assertTrue(holding["in_pool"])
        self.assertEqual(holding["holding_pct"], 100.0)
        self.assertEqual(holding["banned_count"], 0)
        outside = rows["159915"]["strategy"]
        self.assertFalse(outside["is_holding"])
        self.assertFalse(outside["in_pool"])

    def test_no_strategy_returns_no_marks(self):
        resp = self.route(date=DAY.isoformat(), db=self.db)
        self.assertTrue(all(r["strategy"] is None for r in resp.data["rows"]))

    def test_strategy_only_filters_to_pool_and_holdings(self):
        resp = self.route(date=DAY.isoformat(), strategy_id=self.s.id,
                          strategy_only=True, db=self.db)
        self.assertEqual({r["etf_code"] for r in resp.data["rows"]}, {"510300"})
        self.assertEqual(resp.data["total"], 1)

    def test_banned_count_from_failure_signature(self):
        from app.models.experience import Experience
        self.db.add(Experience(
            experience_type="failure", scenario_tags=["高波动"], result="negative",
            title="追高失败", description="重复追高亏损", failure_signature="买入159915后回撤止损",
            occurrence_count=5, is_active=True, generated_date=DAY,
            expires_date=Experience.get_default_expires_date(),
        ))
        self.db.commit()
        resp = self.route(date=DAY.isoformat(), strategy_id=self.s.id, db=self.db)
        rows = {r["etf_code"]: r for r in resp.data["rows"]}
        self.assertEqual(rows["159915"]["strategy"]["banned_count"], 5)

    def test_empty_pool_and_holdings_returns_empty(self):
        db = make_db()
        s = seed_strategy(db, pool={})
        seed_etf(db, "510300", "沪深300ETF")
        resp = self.route(date=DAY.isoformat(), strategy_id=s.id,
                          strategy_only=True, db=db)
        self.assertEqual(resp.data["rows"], [])
        self.assertEqual(resp.data["total"], 0)


class TestSentimentStrategyMarks(unittest.TestCase):
    """舆情接口的策略标注"""

    def setUp(self):
        from app.models.sentiment import SentimentData
        self.db = make_db()
        self.s = seed_strategy(self.db, pool={"510300": 1.0})
        seed_holding(self.db, self.s.id, "159915")
        self.db.add(SentimentData(
            data_date=DAY, source="eastmoney", data_type="news", title="沪深300放量",
            related_etfs=["510300", "159915", "512010"], sentiment_score=0.5,
            sentiment_label="positive", publish_time=datetime(2026, 9, 21, 9, 30),
        ))
        self.db.commit()

    def test_by_date_marks_related_etfs(self):
        from app.routes.auto_strategy_routes import get_sentiments_by_date
        resp = get_sentiments_by_date(target_date=DAY, strategy_id=self.s.id, db=self.db)
        marks = resp.data["strategy_marks"]
        self.assertEqual(set(marks.keys()), {"510300", "159915"})
        self.assertTrue(marks["510300"]["in_pool"])
        self.assertTrue(marks["159915"]["is_holding"])
        self.assertNotIn("512010", marks)

    def test_by_date_without_strategy_has_empty_marks(self):
        from app.routes.auto_strategy_routes import get_sentiments_by_date
        resp = get_sentiments_by_date(target_date=DAY, db=self.db)
        self.assertEqual(resp.data["strategy_marks"], {})

    def test_calendar_related_count(self):
        from app.routes.auto_strategy_routes import get_sentiment_calendar
        resp = get_sentiment_calendar(start_date=DAY, end_date=DAY,
                                      strategy_id=self.s.id, db=self.db)
        day = resp.data["days"][0]
        self.assertEqual(day["strategy_related"], 1)

    def test_calendar_without_strategy_zero(self):
        from app.routes.auto_strategy_routes import get_sentiment_calendar
        resp = get_sentiment_calendar(start_date=DAY, end_date=DAY, db=self.db)
        self.assertEqual(resp.data["days"][0]["strategy_related"], 0)


class TestResearchStrategyMapping(unittest.TestCase):
    """GET /api/research/strategy-mapping"""

    def setUp(self):
        from app.models.stock_fundamental import IndustryScore
        self.db = make_db()
        self.s = seed_strategy(self.db, pool={"512800": 1.0})
        seed_holding(self.db, self.s.id, "512800")
        seed_etf(self.db, "512800", "银行ETF")
        self.db.add(IndustryScore(trade_date=DAY, industry="货币金融业", score=88.0,
                                  rank=1, sample_count=10))
        self.db.commit()

    def test_maps_pool_etf_to_industry(self):
        from app.routes.research_routes import get_strategy_mapping
        resp = get_strategy_mapping(strategy_id=self.s.id, db=self.db)
        etfs = resp.data["etfs"]
        self.assertEqual(len(etfs), 1)
        self.assertEqual(etfs[0]["industry"], "货币金融业")
        self.assertEqual(etfs[0]["rank"], 1)
        self.assertTrue(etfs[0]["is_holding"])
        self.assertEqual(resp.data["industries"], {"货币金融业": ["512800"]})

    def test_no_strategy_id_returns_empty(self):
        from app.routes.research_routes import get_strategy_mapping
        resp = get_strategy_mapping(db=self.db)
        self.assertEqual(resp.data["etfs"], [])
        self.assertEqual(resp.data["industries"], {})

    def test_no_industry_score_degrades(self):
        from app.routes.research_routes import get_strategy_mapping
        self.s.allocation_config = {"512800": 0.5, "512880": 0.5}
        self.db.commit()
        seed_etf(self.db, "512880", "证券ETF")
        resp = get_strategy_mapping(strategy_id=self.s.id, db=self.db)
        # 无行业匹配的池内标的仍返回，行业字段为空
        etfs = {e["etf_code"]: e for e in resp.data["etfs"]}
        self.assertEqual(set(etfs.keys()), {"512800", "512880"})
        self.assertEqual(etfs["512800"]["industry"], "货币金融业")
        self.assertIsNone(etfs["512880"]["industry"])


if __name__ == "__main__":
    unittest.main()