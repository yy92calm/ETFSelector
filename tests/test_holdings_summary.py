"""策略持仓 summary 测试（总资产/资产净值/现金）"""
import unittest

from app.routes.portfolio_routes import get_holdings


def make_db():
    """内存 SQLite + 全表 schema"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_strategy_and_snapshot(db, initial_capital=100000, with_snapshot=True):
    from app.models.strategy import Strategy
    from app.models.portfolio import PortfolioSnapshot
    from datetime import date

    s = Strategy(name="测试策略", allocation_config={"510300": 1.0}, initial_capital=initial_capital)
    db.add(s)
    db.commit()
    if with_snapshot:
        db.add(PortfolioSnapshot(
            strategy_id=s.id, trade_date=date(2026, 9, 1),
            total_asset=105000.0, cash=5000.0, market_value=100000.0,
            profit=1000.0, profit_pct=5.0,
        ))
        db.commit()
    return s


class TestHoldingsSummary(unittest.TestCase):
    """GET /api/portfolio/{id}/holdings 的 summary 字段"""

    def test_summary_with_snapshot(self):
        from app.models.portfolio import PortfolioSnapshot
        db = make_db()
        s = seed_strategy_and_snapshot(db)
        # 满足路由签名（db 手动传入，不走 Depends）
        resp = get_holdings(s.id, db)
        summary = resp.data["summary"]
        self.assertEqual(summary["total_asset"], 105000.0)
        self.assertEqual(summary["cash"], 5000.0)
        self.assertEqual(summary["market_value"], 100000.0)
        self.assertEqual(summary["nav"], 1.05)          # 105000/100000
        self.assertEqual(summary["as_of"], "2026-09-01")

    def test_summary_without_snapshot_falls_back_to_initial(self):
        db = make_db()
        s = seed_strategy_and_snapshot(db, with_snapshot=False)
        resp = get_holdings(s.id, db)
        summary = resp.data["summary"]
        self.assertEqual(summary["total_asset"], 100000.0)
        self.assertEqual(summary["cash"], 100000.0)
        self.assertEqual(summary["market_value"], 0.0)
        self.assertEqual(summary["nav"], 1.0)
        self.assertIsNone(summary["as_of"])

    def test_summary_takes_latest_snapshot(self):
        """多日快照时取最新一条"""
        from app.models.portfolio import PortfolioSnapshot
        from datetime import date
        db = make_db()
        s = seed_strategy_and_snapshot(db)
        db.add(PortfolioSnapshot(
            strategy_id=s.id, trade_date=date(2026, 9, 2),
            total_asset=110000.0, cash=10000.0, market_value=100000.0,
            profit=500.0, profit_pct=10.0,
        ))
        db.commit()
        resp = get_holdings(s.id, db)
        self.assertEqual(resp.data["summary"]["total_asset"], 110000.0)
        self.assertEqual(resp.data["summary"]["as_of"], "2026-09-02")

    def test_holdings_key_still_present(self):
        """向后兼容：holdings 列表字段不动"""
        db = make_db()
        s = seed_strategy_and_snapshot(db, with_snapshot=False)
        resp = get_holdings(s.id, db)
        self.assertIn("holdings", resp.data)
        self.assertIsInstance(resp.data["holdings"], list)


if __name__ == "__main__":
    unittest.main()
