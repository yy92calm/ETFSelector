"""单ETF因子画像接口测试（/api/factors/etf）"""
import unittest

from app.routes.factor_routes import get_etf_factor_profile


def make_db():
    """内存 SQLite + 全表 schema"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class TestEtfFactorProfile(unittest.TestCase):
    """GET /api/factors/etf?etf_code= 的聚合逻辑"""

    def test_latest_date_aggregation(self):
        from datetime import date
        from app.models.etf import ETFDailyIndicator
        from app.models.factor_performance import FactorPerformance
        db = make_db()

        # 旧日期数据（应被最新日期覆盖）+ 最新日期 3 个因子
        for d, scores in ((date(2026, 8, 1), {"momentum": 10.0}),
                          (date(2026, 8, 14), {"momentum": 82.5, "trend": 66.7, "volume": 40.0})):
            for name, val in scores.items():
                db.add(FactorPerformance(etf_code="510300", trade_date=d,
                                         factor_name=name, factor_value=val))
        # 同日其他 ETF（不应混入）
        db.add(FactorPerformance(etf_code="159915", trade_date=date(2026, 8, 14),
                                 factor_name="momentum", factor_value=99.0))
        db.add(ETFDailyIndicator(etf_code="510300", trade_date=date(2026, 8, 14),
                                 composite_score=78.5, rank_in_market=12))
        db.commit()

        resp = get_etf_factor_profile(etf_code="510300", db=db)
        data = resp.data
        self.assertEqual(data["trade_date"], "2026-08-14")
        self.assertEqual(data["composite_score"], 78.5)
        self.assertEqual(data["rank"], 12)
        self.assertEqual(data["factor_scores"], {"momentum": 82.5, "trend": 66.7, "volume": 40.0})

    def test_no_factor_rows_returns_none(self):
        db = make_db()
        resp = get_etf_factor_profile(etf_code="510300", db=db)
        self.assertIsNone(resp.data)

    def test_indicator_missing_fields_none(self):
        """有因子记录但无当日指标行时，综合分/排名应为 None"""
        from datetime import date
        from app.models.factor_performance import FactorPerformance
        db = make_db()
        db.add(FactorPerformance(etf_code="510300", trade_date=date(2026, 8, 14),
                                 factor_name="momentum", factor_value=50.0))
        db.commit()

        resp = get_etf_factor_profile(etf_code="510300", db=db)
        self.assertIsNone(resp.data["composite_score"])
        self.assertIsNone(resp.data["rank"])
        self.assertEqual(resp.data["factor_scores"], {"momentum": 50.0})


if __name__ == "__main__":
    unittest.main()
