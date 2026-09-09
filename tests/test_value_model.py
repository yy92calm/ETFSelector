"""盈利-估值性价比模型测试（行业评分/个股筛选/报告期边界）"""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from app.services.value_model_service import ValueModelService
from app.services.fundamental_data_service import FundamentalDataService


def make_db():
    """内存 SQLite + 全表 schema"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_stock(db, code, name, industry, end, days=40, pe_series=None, ni_yoy=30.0):
    """按天落一只股票的基本面行；pe_series 为空则恒定 PE"""
    from app.models.stock_fundamental import StockFundamental
    for i in range(days):
        d = end - timedelta(days=days - 1 - i)
        pe = pe_series(i) if callable(pe_series) else (pe_series if pe_series else 20.0)
        db.add(StockFundamental(
            stock_code=code, stock_name=name, trade_date=d, industry=industry,
            close=10.0, pe_ttm=pe, pb_mrq=2.0,
            ni_yoy=ni_yoy, growth_stat_date=date(2026, 3, 31),
        ))
    db.commit()


class TestIndustryScores(unittest.TestCase):
    """compute_industry_scores 聚合与排名"""

    def setUp(self):
        self.db = make_db()
        self.end = date(2026, 9, 9)

    def test_high_growth_low_pe_industry_ranks_first(self):
        svc = ValueModelService()
        # 医药：低PE高增长；钢铁：高PE低增长
        for i in range(6):
            seed_stock(self.db, f"sh.6001{i:02d}", f"药股{i}", "医药制造业",
                       self.end, pe_series=lambda i: 15.0, ni_yoy=40.0)
        for i in range(6):
            seed_stock(self.db, f"sh.6002{i:02d}", f"钢股{i}", "黑色金属冶炼",
                       self.end, pe_series=lambda i: 40.0, ni_yoy=2.0)

        n = svc.compute_industry_scores(self.db, self.end)
        self.assertEqual(n, 2)
        ranking = svc.get_industry_ranking(self.db)
        self.assertEqual(len(ranking), 2)
        top = ranking[0]
        self.assertEqual(top.industry, "医药制造业")
        self.assertEqual(top.rank, 1)
        self.assertLess(top.median_pe, 20)
        self.assertGreater(top.median_growth, 30)
        self.assertIsNotNone(top.peg)
        self.assertLess(top.peg, 0.5)

    def test_small_industry_excluded(self):
        svc = ValueModelService()
        for i in range(3):  # 样本<5 不参评
            seed_stock(self.db, f"sz.0001{i}", f"小行业{i}", "综合",
                       self.end, ni_yoy=50.0)
        n = svc.compute_industry_scores(self.db, self.end)
        self.assertEqual(n, 0)


class TestStockPicks(unittest.TestCase):
    """get_stock_picks 筛选逻辑"""

    def setUp(self):
        self.db = make_db()
        self.end = date(2026, 9, 9)

    def test_filters_and_ordering(self):
        svc = ValueModelService()
        # 达标：低PE高增长 → PEG 0.2，应排第一
        seed_stock(self.db, "sh.600000", "甲", "医药制造业", self.end,
                   pe_series=lambda i: 10.0, ni_yoy=50.0)
        # PEG超限：PE 100 / 增长10 → PEG 10，应被剔除
        seed_stock(self.db, "sh.600001", "乙", "医药制造业", self.end,
                   pe_series=lambda i: 100.0, ni_yoy=10.0)
        # 负增长：应被剔除
        seed_stock(self.db, "sh.600002", "丙", "黑色金属冶炼", self.end,
                   pe_series=lambda i: 8.0, ni_yoy=-5.0)
        # 达标但PEG更高：PE 30 / 增长 40 → 0.75，排第二
        seed_stock(self.db, "sh.600003", "丁", "黑色金属冶炼", self.end,
                   pe_series=lambda i: 30.0, ni_yoy=40.0)

        picks = svc.get_stock_picks(self.db, top_n=10)
        codes = [p["stock_code"] for p in picks]
        self.assertIn("600000", codes)
        self.assertIn("600003", codes)
        self.assertNotIn("600001", codes)
        self.assertNotIn("600002", codes)
        self.assertEqual(codes[0], "600000")
        self.assertGreater(picks[0]["score"], picks[-1]["score"])
        self.assertIsNotNone(picks[0]["pe_percentile"])

    def test_empty_db_returns_empty(self):
        svc = ValueModelService()
        self.assertEqual(svc.get_stock_picks(make_db()), [])


class TestRecentQuarters(unittest.TestCase):
    """报告期披露节奏边界"""

    def test_boundaries(self):
        f = FundamentalDataService.recent_quarters
        # 三季报10月末才披露完毕，9月初最新已披露为半年报
        self.assertEqual(f(date(2026, 9, 9)), [(2026, 2), (2026, 1)])
        self.assertEqual(f(date(2026, 10, 31)), [(2026, 3), (2026, 2)])
        self.assertEqual(f(date(2026, 8, 31)), [(2026, 2), (2026, 1)])
        self.assertEqual(f(date(2026, 6, 15)), [(2026, 1), (2025, 4)])
        self.assertEqual(f(date(2026, 4, 30)), [(2026, 1), (2025, 4)])
        self.assertEqual(f(date(2026, 3, 1)), [(2025, 3), (2025, 2)])


if __name__ == "__main__":
    unittest.main()
