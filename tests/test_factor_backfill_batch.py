"""因子回填：批量预加载行情后结果与逐条查询等价，且 SQL 往返不随记录数增长"""
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models.etf import ETFDailyIndicator, ETFQuotation
from app.models.factor_performance import FactorPerformance
from app.services.factor_performance_service import FactorPerformanceService, FACTORS

START = date(2026, 8, 3)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def count_queries(engine) -> list:
    """挂监听统计 SQL 往返次数"""
    statements = []
    event.listen(engine, "before_cursor_execute",
                 lambda c, cur, sql, *a: statements.append(sql))
    return statements


def seed_prices(db, code, prices):
    """按 {日序号: 收盘价} 造行情"""
    for offset, price in prices.items():
        db.add(ETFQuotation(etf_code=code, trade_date=START + timedelta(days=offset),
                            open_price=price, close_price=price,
                            high_price=price, low_price=price, volume=1, amount=1))


def expected_forward_return(prices: dict, trade_date_offset: int):
    """逐条查询口径：T 之后第5个有价交易日 vs T 日收盘"""
    base = prices.get(trade_date_offset)
    future = sorted(o for o in prices if o > trade_date_offset)
    if base is None or len(future) < 5:
        return None
    target = prices[future[4]]
    if base <= 0 or target is None:
        return None
    return round((target - base) / base * 100, 4)


class TestForwardReturnBatch(unittest.TestCase):

    def setUp(self):
        self.db, self.engine = make_db()
        self.service = FactorPerformanceService()

    def tearDown(self):
        self.db.close()

    def _seed_history(self, code, day_count, step=0.01):
        prices = {i: round(10 * (1 + step) ** i, 4) for i in range(day_count)}
        seed_prices(self.db, code, prices)
        return prices

    def _add_pending(self, code, offsets, factor="momentum"):
        for offset in offsets:
            self.db.add(FactorPerformance(etf_code=code,
                                          trade_date=START + timedelta(days=offset),
                                          factor_name=factor, factor_value=50.0))

    def test_values_match_per_code_reference(self):
        """批量结果与逐条口径逐条相等"""
        prices_a = self._seed_history("510300", 40, step=0.007)
        prices_b = self._seed_history("510500", 40, step=0.013)
        self._add_pending("510300", [0, 5, 12, 20, 33])
        self._add_pending("510500", [1, 9, 25, 36], factor="trend")
        self.db.commit()

        # 510500 的 36 日之后只剩 3 个交易日，不满足 T+5，故回填 8 条
        self.assertEqual(self.service.backfill_forward_returns(self.db), 8)

        rows = self.db.query(FactorPerformance).order_by(FactorPerformance.id).all()
        for row in rows:
            offset = (row.trade_date - START).days
            prices = prices_a if row.etf_code == "510300" else prices_b
            self.assertEqual(row.forward_return_5d, expected_forward_return(prices, offset),
                             f"{row.etf_code} {offset}日 回填值不一致")

    def test_missing_t5_window_is_not_filled(self):
        """T 之后不足 5 个交易日保持为空（下次运行仍会重试）"""
        self._seed_history("510300", 12)
        self._add_pending("510300", [0, 7, 11])
        self.db.commit()

        filled = self.service.backfill_forward_returns(self.db)

        self.assertEqual(filled, 1)
        by_offset = {(r.trade_date - START).days: r.forward_return_5d
                     for r in self.db.query(FactorPerformance).all()}
        self.assertIsNotNone(by_offset[0])
        self.assertIsNone(by_offset[7])
        self.assertIsNone(by_offset[11])

    def test_query_count_is_constant(self):
        """待回填记录数增长一个量级，SQL 往返次数不变"""
        small, filled_small = self._run_isolated(3)
        large, filled_large = self._run_isolated(25)

        self.assertEqual(small, large)
        self.assertEqual(filled_small, 3 * 3)
        self.assertEqual(filled_large, 25 * 3)

    @staticmethod
    def _run_isolated(offsets_count: int):
        """独立内存库造 offsets_count × 3 个代码的待回填记录，返回 (SQL次数, 回填条数)"""
        db, engine = make_db()
        try:
            prices = {i: round(10 * 1.01 ** i, 4) for i in range(60)}
            for code in ("510300", "510500", "159915"):
                seed_prices(db, code, prices)
                for offset in range(offsets_count):
                    db.add(FactorPerformance(
                        etf_code=code, trade_date=START + timedelta(days=offset),
                        factor_name="momentum", factor_value=50.0))
            db.commit()

            statements = count_queries(engine)
            filled = FactorPerformanceService().backfill_forward_returns(db)
            return len(statements), filled
        finally:
            db.close()

    def test_null_close_price_not_counted_as_trading_day(self):
        """收盘价为空的行情不算交易日，T+5 只数有效报价日"""
        prices = {i: 10 + i for i in range(10)}
        prices[4] = None
        seed_prices(self.db, "510300", {k: v for k, v in prices.items() if v is not None})
        self._add_pending("510300", [0])
        self.db.commit()

        self.assertEqual(self.service.backfill_forward_returns(self.db), 1)
        row = self.db.query(FactorPerformance).first()
        # 有效报价日中 T 之后第5个是第6天（第4天无收盘价，被排除在计数外）
        self.assertEqual(row.forward_return_5d, round((prices[6] - prices[0]) / prices[0] * 100, 4))

    def test_empty_pending_returns_zero(self):
        self.assertEqual(self.service.backfill_forward_returns(self.db), 0)


class TestBackfillFromIndicators(unittest.TestCase):

    def setUp(self):
        self.db, self.engine = make_db()
        self.service = FactorPerformanceService()

    def tearDown(self):
        self.db.close()

    def _add_indicator(self, code, trade_date):
        self.db.add(ETFDailyIndicator(
            etf_code=code, trade_date=trade_date,
            momentum_5d=0.02, momentum_20d=0.05, momentum_score=60.0,
            trend_strength=1.2, ma5=1, ma10=1, ma20=1, vol_ratio=1.1,
            volatility_20d=0.18, obv_slope=1000.0, amount_avg_5d=2e7,
            composite_score=70.0,
        ))

    def test_partial_day_is_filled_in_not_skipped(self):
        """某日只落了部分因子时，重跑应补齐缺失因子而不是跳过整日"""
        d = START + timedelta(days=1)
        self._add_indicator("510300", d)
        self.db.commit()
        self.service.backfill_from_indicators(self.db)

        # 人为删掉两个因子，模拟历史缺失
        for name in ("trend", "capital_flow"):
            self.db.query(FactorPerformance).filter(
                FactorPerformance.factor_name == name).delete()
        self.db.commit()

        added = self.service.backfill_from_indicators(self.db)

        self.assertEqual(added, 2)
        names = {r.factor_name for r in self.db.query(FactorPerformance).all()}
        self.assertEqual(names, set(FACTORS))

    def test_other_code_same_date_still_written(self):
        """同日不同代码是不同记录，不应被同日已有记录挡掉"""
        d = START + timedelta(days=2)
        self._add_indicator("510300", d)
        self._add_indicator("510500", d)
        self.db.commit()

        added = self.service.backfill_from_indicators(self.db)

        self.assertEqual(added, len(FACTORS) * 2)
        codes = {r.etf_code for r in self.db.query(FactorPerformance).all()}
        self.assertEqual(codes, {"510300", "510500"})

    def test_rerun_is_idempotent(self):
        d = START + timedelta(days=3)
        self._add_indicator("510300", d)
        self._add_indicator("510500", d)
        self.db.commit()
        first = self.service.backfill_from_indicators(self.db)

        second = self.service.backfill_from_indicators(self.db)

        self.assertEqual(first, len(FACTORS) * 2)
        self.assertEqual(second, 0)
        self.assertEqual(self.db.query(FactorPerformance).count(), len(FACTORS) * 2)

    def test_no_indicators_returns_zero(self):
        self.assertEqual(self.service.backfill_from_indicators(self.db), 0)


if __name__ == "__main__":
    unittest.main()
