"""市场状态刻画服务测试（风险偏好/风格轮动/基金分化度/仓位信号）"""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd

from app.services.market_regime_service import (
    MarketRegimeService,
    BETA_PROXIES, DEFENSIVE_PROXIES, LARGE_CAP_PROXIES,
    SMALL_CAP_PROXIES, GROWTH_PROXIES, VALUE_PROXIES,
)


def make_db():
    """内存 SQLite + 全表 schema"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class TestMarketRegime(unittest.TestCase):
    """compute() 在合成行情下的完整链路"""

    def setUp(self):
        self.db = make_db()
        self.end = date(2026, 9, 7)
        self._seed_pool()

    def _seed_pool(self):
        """合成 70 个交易日行情：高beta/小盘/成长上行，防御/价值下行，成交额递增"""
        from app.models.etf import ETFDailyIndicator, ETFQuotation
        plan = {}
        for c in BETA_PROXIES + GROWTH_PROXIES + SMALL_CAP_PROXIES:
            plan[c] = 0.5   # 日涨0.5% → 20日动量约+10.5%
        for c in DEFENSIVE_PROXIES + VALUE_PROXIES:
            plan[c] = -0.2  # 日跌0.2% → 20日动量约-4%
        for c in LARGE_CAP_PROXIES:
            plan[c] = 0.1
        for code, chg in plan.items():
            price = 1.0
            for i in range(70):
                d = self.end - timedelta(days=69 - i)
                price *= 1 + chg / 100
                self.db.add(ETFQuotation(
                    etf_code=code, trade_date=d, close_price=round(price, 4),
                    amount=1e8 * (1 + i / 69 * 3), volume=1000,
                ))
        # 指标表恒定波动率 → 分位为 0（低波动加分路径）
        for i in range(70):
            d = self.end - timedelta(days=69 - i)
            self.db.add(ETFDailyIndicator(
                etf_code="510300", trade_date=d,
                volatility_20d=25.0, composite_score=50.0,
            ))
        self.db.commit()

    def test_aggressive_scenario_full_chain(self):
        """高beta强/防御弱 → 进取偏好、成长与小盘占优、机会状态"""
        svc = MarketRegimeService()
        with patch.object(MarketRegimeService, "_compute_fund_dispersion",
                          return_value=(18.5, "分化", {"valid_samples": 40})):
            snap = svc.compute(self.end, self.db)

        self.assertIsNotNone(snap.risk_appetite)
        self.assertGreater(snap.risk_appetite, 65)
        self.assertEqual(snap.risk_label, "进取")
        self.assertEqual(snap.style_rotation["size"]["leading"], "小盘")
        self.assertEqual(snap.style_rotation["growth_value"]["leading"], "成长")
        self.assertEqual(snap.market_state, "opportunity")
        # 进取 + 分化 → 权益上限回落
        self.assertEqual(snap.suggested_equity_range, [0.6, 0.8])
        self.assertIn("建议权益仓位", snap.state_note)

    def test_compute_idempotent(self):
        svc = MarketRegimeService()
        with patch.object(MarketRegimeService, "_compute_fund_dispersion",
                          return_value=(10.0, "适度", {})):
            svc.compute(self.end, self.db)
            snap2 = svc.compute(self.end, self.db)
        rows = self.db.query(type(snap2)).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(snap2.dispersion_label, "适度")

    def test_missing_data_falls_back_neutral(self):
        """空库（无行情）→ 风险偏好缺失 → 中性兜底区间"""
        from app.models.market_regime import MarketRegimeSnapshot
        db = make_db()
        svc = MarketRegimeService()
        with patch.object(MarketRegimeService, "_compute_fund_dispersion",
                          return_value=(None, None, {"error": "样本不足"})):
            snap = svc.compute(self.end, db)
        self.assertIsNone(snap.risk_appetite)
        self.assertEqual(snap.market_state, "neutral")
        self.assertEqual(snap.suggested_equity_range, [0.4, 0.7])


class TestFundDispersion(unittest.TestCase):
    """基金收益率分化度计算（mock 净值数据源）"""

    def test_dispersion_from_mock_navs(self):
        svc = MarketRegimeService()
        navs = {}
        dates = pd.date_range("2026-06-01", periods=62).strftime("%Y-%m-%d")
        for i in range(35):
            nav, rows = 1.0, []
            for j, d in enumerate(dates):
                nav *= 1 + 0.001 * (i % 5 - 2)
                rows.append({"日期": d, "累计净值": round(nav, 4)})
            navs[f"{i:06d}"] = pd.DataFrame(rows)

        with patch("app.services.data_sources.get_data_source_manager") as gm, \
             patch.object(MarketRegimeService, "_sample_fund_codes",
                          return_value=list(navs.keys())):
            gm.return_value.fetch_fund_nav_batch.return_value = navs
            std, label, detail = svc._compute_fund_dispersion()

        self.assertIsNotNone(std)
        self.assertGreater(std, 0)
        self.assertIn(label, ("一致", "适度", "分化"))
        self.assertEqual(detail["valid_samples"], 35)

    def test_insufficient_samples_returns_none(self):
        svc = MarketRegimeService()
        dates = pd.date_range("2026-06-01", periods=62).strftime("%Y-%m-%d")
        navs = {"000001": pd.DataFrame(
            [{"日期": d, "累计净值": 1.0} for d in dates])}
        with patch("app.services.data_sources.get_data_source_manager") as gm, \
             patch.object(MarketRegimeService, "_sample_fund_codes",
                          return_value=list(navs.keys())):
            gm.return_value.fetch_fund_nav_batch.return_value = navs
            std, label, detail = svc._compute_fund_dispersion()
        self.assertIsNone(std)
        self.assertIsNone(label)
        self.assertIn("error", detail)


if __name__ == "__main__":
    unittest.main()
