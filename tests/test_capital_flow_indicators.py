"""资金流向指标 JSON 可序列化：np.float64 比较产出 numpy 标量 bool（类名恰为 "bool"），
json.dumps 会拒绝，曾使管道 policy_flow 阶段在持仓 5 日价跌日连日失败"""

import json
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
# create_all 只建「已导入模型」的表，故先导入全部模型（与 init_db 口径一致）
from app.models import (  # noqa: F401
    allocation_suggestion, auto_strategy_log, chat, etf, experience, factor_performance,
    market_regime, pipeline_checkpoint, portfolio, sentiment, stock_fundamental, strategy,
    sw_industry, system_config, task_log,
)
from app.models.etf import ETFQuotation
from app.services.capital_flow_service import CapitalFlowService

TODAY = date.today()
N = 20


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_quotes(db, code: str, closes, volumes):
    """按日期升序写 N 根日K（closes/volumes 等长）"""
    for i, (c, v) in enumerate(zip(closes, volumes)):
        db.add(ETFQuotation(
            etf_code=code, trade_date=TODAY - timedelta(days=N - 1 - i),
            open_price=c, close_price=c, high_price=c, low_price=c,
            volume=v, amount=c * v, change_pct=0,
        ))
    db.commit()


def linear(start: float, end: float) -> list:
    return [start + (end - start) * i / (N - 1) for i in range(N)]


class TestFlowIndicatorsJson(unittest.TestCase):

    def setUp(self):
        self.db = make_db()
        self.svc = CapitalFlowService()

    def _dump(self, code):
        payload = self.svc._compute_flow_indicators([code], self.db)
        self.assertIn(code, payload, "至少 10 根日K时应产出指标")
        # 修复前：divergence 为 numpy 标量 bool，此行抛
        # TypeError: Object of type bool is not JSON serializable
        json.dumps(payload, ensure_ascii=False, indent=2)
        return payload[code]

    def test_down_day_is_serializable(self):
        """价跌日（任意量能）此前必炸——第二分支必产出 numpy bool"""
        seed_quotes(self.db, "510300", linear(2.0, 1.0), [1000.0] * N)
        row = self._dump("510300")
        self.assertIsInstance(row["price_volume_divergence"], bool)
        self.assertFalse(row["price_volume_divergence"])  # 缩量下跌不算背离

    def test_down_day_with_volume_surge_is_divergent(self):
        """价跌 + 量能放大 ≥ 1.3 → 背离为 True 且可序列化"""
        seed_quotes(self.db, "512100", linear(2.0, 1.0), [500.0] * 15 + [1000.0] * 5)
        row = self._dump("512100")
        self.assertIsInstance(row["price_volume_divergence"], bool)
        self.assertTrue(row["price_volume_divergence"])

    def test_up_day_with_shrinking_volume_is_divergent(self):
        """价涨 + 缩量 < 0.8 → 背离为 True 且可序列化"""
        seed_quotes(self.db, "515220", linear(1.0, 2.0), [1000.0] * 15 + [500.0] * 5)
        row = self._dump("515220")
        self.assertIsInstance(row["price_volume_divergence"], bool)
        self.assertTrue(row["price_volume_divergence"])

    def test_up_day_with_normal_volume(self):
        """价涨 + 量正常 → 无背离（回归：该分支此前恰好产出内置 bool 不炸）"""
        seed_quotes(self.db, "511200", linear(1.0, 2.0), [1000.0] * N)
        row = self._dump("511200")
        self.assertIsInstance(row["price_volume_divergence"], bool)
        self.assertFalse(row["price_volume_divergence"])

    def test_multiple_codes_serializable(self):
        """多标的混合行情整体可序列化（与线上 analyze_capital_flow 的 prompt 拼装同路径）"""
        seed_quotes(self.db, "510300", linear(2.0, 1.0), [1000.0] * N)
        seed_quotes(self.db, "515220", linear(1.0, 2.0), [1000.0] * 15 + [500.0] * 5)
        payload = self.svc._compute_flow_indicators(["510300", "515220"], self.db)
        json.dumps(payload, ensure_ascii=False)
        for code in ("510300", "515220"):
            self.assertIsInstance(payload[code]["price_volume_divergence"], bool)


if __name__ == "__main__":
    unittest.main()
