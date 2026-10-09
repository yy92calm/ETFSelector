"""市场扫描批量预加载：与逐只查询结果等价，并消除 N+1 查询"""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models.etf import ETFBasic, ETFQuotation, ETFDailyIndicator
from app.services import market_scanner_service
from app.services.market_scanner_service import MarketScannerService

END_DATE = date(2026, 9, 4)


def make_db():
    """内存 SQLite + 全表 schema"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def seed(db, history_by_code):
    """按 {code: 交易日数} 造行情，含历史不足的代码以覆盖跳过分支。

    每个代码用不同的涨幅与量能，避免综合分并列导致排名不可比较。
    """
    for idx, (code, n) in enumerate(sorted(history_by_code.items())):
        db.add(ETFBasic(etf_code=code, etf_name=f"{code}测试"))
        step = 0.002 * (idx + 1)      # 不同代码不同日涨幅
        price = 1.0
        for i in range(n):
            d = END_DATE - timedelta(days=n - 1 - i)
            price *= 1 + (step if i % 3 else -step / 2)
            db.add(ETFQuotation(
                etf_code=code, trade_date=d,
                open_price=round(price, 4), close_price=round(price, 4),
                high_price=round(price * 1.01, 4), low_price=round(price * 0.99, 4),
                volume=(1000 + i * 37) * (idx + 1),
                amount=(2e7 + i * 100_000) * (idx + 1),
                change_pct=0.5,
            ))
    db.commit()


HISTORY = {
    "510300": 40,   # 超过 30 条，验证只取最近 30 条
    "510500": 30,   # 恰好 30 条
    "159915": 26,   # 刚好过 MIN_HISTORY_DAYS 门槛
    "512880": 24,   # 不足 25 条，应被跳过
    "588000": 35,
}

INDICATOR_FIELDS = [
    "momentum_5d", "momentum_20d", "momentum_score", "trend_strength",
    "ma5", "ma10", "ma20", "vol_ratio", "volatility_20d", "obv_slope",
    "amount_avg_5d", "composite_score",
]


class TestBatchEquivalence(unittest.TestCase):
    """批量路径（scan_all）与逐只路径（_compute_indicator 自查）结果必须一致"""

    def setUp(self):
        self.db, self.engine = make_db()
        seed(self.db, HISTORY)
        self.scanner = MarketScannerService()

    def tearDown(self):
        self.db.close()

    def _legacy_results(self):
        """逐只路径：不传 quotes/weights，由 _compute_indicator 自己查库"""
        out = {}
        for code in HISTORY:
            ind = self.scanner._compute_indicator(code, END_DATE, self.db)
            if ind:
                out[code] = ind
        return out

    def test_batch_matches_per_code(self):
        legacy = self._legacy_results()
        self.assertTrue(legacy, "逐只路径应有产出")
        # 历史不足 25 条的应被门槛过滤掉
        self.assertNotIn("512880", legacy)

        result = self.scanner.scan_all(END_DATE, self.db)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], len(legacy))

        rows = self.db.query(ETFDailyIndicator).filter(
            ETFDailyIndicator.trade_date == END_DATE
        ).all()
        self.assertEqual(len(rows), len(legacy))
        for row in rows:
            expected = legacy[row.etf_code]
            for f in INDICATOR_FIELDS:
                self.assertEqual(
                    getattr(row, f), expected[f],
                    msg=f"{row.etf_code} 字段 {f} 批量路径与逐只路径不一致")

    def test_ranks_match(self):
        """排名按综合分降序，批量路径不应改变相对次序"""
        legacy = self._legacy_results()
        self.scanner.scan_all(END_DATE, self.db)
        rows = self.db.query(ETFDailyIndicator).filter(
            ETFDailyIndicator.trade_date == END_DATE
        ).all()
        expected_ranks = {
            r.etf_code: i + 1
            for i, r in enumerate(
                sorted(rows, key=lambda x: x.composite_score, reverse=True))
        }
        for r in rows:
            self.assertEqual(r.rank_in_market, expected_ranks[r.etf_code])

    def test_quote_query_count_does_not_scale_with_etf_count(self):
        """行情查询次数应为常数级，不随 ETF 数量线性增长（消除 N+1）"""
        def count_quote_queries(code_count: int) -> int:
            db, engine = make_db()
            seed(db, {f"{500000 + i:06d}": 40 for i in range(code_count)})
            captured = []

            def listen(conn, cursor, statement, params, context, executemany):
                if "FROM etf_quotation" in statement.lower():
                    captured.append(statement)

            event.listen(engine, "before_cursor_execute", listen)
            try:
                MarketScannerService().scan_all(END_DATE, db)
            finally:
                event.remove(engine, "before_cursor_execute", listen)
            db.close()
            return len(captured)

        small = count_quote_queries(10)
        large = count_quote_queries(60)
        # 6倍标的数，查询次数不应增长（历史不足者才有少量补查）
        self.assertEqual(small, large,
                         msg=f"查询次数随标的数增长: {small} -> {large}，疑似 N+1")
        self.assertLessEqual(large, 3,
                             msg=f"查询次数 {large} 偏高，应接近常数")

    def test_adaptive_weights_resolved_once(self):
        """自适应权重不得在每只ETF的循环里重复获取"""
        with patch.object(MarketScannerService, "_resolve_weights",
                          wraps=self.scanner._resolve_weights) as spy:
            self.scanner.scan_all(END_DATE, self.db)
            self.assertEqual(spy.call_count, 1)

    def test_resolve_weights_falls_back_to_fixed(self):
        """无IC数据/异常时使用固定权重"""
        with patch.object(market_scanner_service, "WEIGHTS", market_scanner_service.WEIGHTS):
            self.assertEqual(self.scanner._resolve_weights(self.db), market_scanner_service.WEIGHTS)

        boom = unittest.mock.MagicMock(side_effect=RuntimeError("db down"))
        with patch("app.services.factor_performance_service.get_factor_performance_service", boom):
            self.assertEqual(self.scanner._resolve_weights(self.db), market_scanner_service.WEIGHTS)

    def test_resolve_weights_uses_adaptive(self):
        fake = unittest.mock.MagicMock()
        fake.get_adaptive_weights.return_value = {"momentum": 0.5, "trend": 0.2,
                                                  "volume": 0.1, "volatility": 0.1,
                                                  "capital_flow": 0.1}
        with patch("app.services.factor_performance_service.get_factor_performance_service",
                   return_value=fake):
            w = self.scanner._resolve_weights(self.db)
        self.assertEqual(w["momentum"], 0.5)


class TestSparseBackfillCompat(unittest.TestCase):
    """scripts/backfill_sparse_indicators.py 依赖的单只调用签名仍可用"""

    def test_single_code_still_works(self):
        db, _ = make_db()
        seed(db, HISTORY)
        scanner = MarketScannerService()
        ind = scanner._compute_indicator("510300", END_DATE, db)
        self.assertIsNotNone(ind)
        self.assertEqual(ind["etf_code"], "510300")
        # 取的是最近 30 条而非更早数据：批量与单只要一致
        batch = scanner._load_quotes_map(["510300"], END_DATE, db)
        self.assertEqual(len(batch["510300"]), 30)
        legacy_only = scanner._compute_indicator("510300", END_DATE, db)
        self.assertEqual(ind["composite_score"], legacy_only["composite_score"])
        db.close()

    def test_load_quotes_map_empty_codes(self):
        db, _ = make_db()
        self.assertEqual(MarketScannerService()._load_quotes_map([], END_DATE, db), {})
        db.close()

    def test_sparse_history_is_top_up(self):
        """历史间隔稀疏、落在窗口内的条数不足 30 时，必须回退逐只补全到真正的最近 30 条"""
        db, _ = make_db()
        code = "159999"
        db.add(ETFBasic(etf_code=code, etf_name="稀疏测试"))
        price = 1.0
        # 每 10 天一条，窗口(90天)内只有约 9 条，但全历史有 40 条
        for i in range(40):
            d = END_DATE - timedelta(days=(39 - i) * 10)
            price *= 1.003
            db.add(ETFQuotation(
                etf_code=code, trade_date=d,
                open_price=round(price, 4), close_price=round(price, 4),
                high_price=round(price, 4), low_price=round(price, 4),
                volume=5000 + i, amount=8e7, change_pct=0.3,
            ))
        db.commit()

        scanner = MarketScannerService()
        batch = scanner._load_quotes_map([code], END_DATE, db)
        legacy = scanner._load_quotes_for_code(code, END_DATE, db)
        legacy.reverse()

        self.assertEqual(len(batch[code]), 30, "应补全到最近30条")
        self.assertEqual(batch[code], legacy, "稀疏标的批量结果应与逐只一致")

        ind = scanner._compute_indicator(code, END_DATE, db, quotes=batch[code])
        self.assertIsNotNone(ind, "补全后应能通过最短历史门槛")
        db.close()


if __name__ == "__main__":
    unittest.main()
