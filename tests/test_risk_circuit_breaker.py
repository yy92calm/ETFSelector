"""风控熔断口径与冷却自动恢复测试"""
import unittest
from datetime import date, datetime, timedelta

from tests.test_value_model import make_db

TODAY = date(2026, 9, 25)


def seed_strategy(db, sid=1, initial=1000000.0, status="running",
                  paused_date=None, cooldown=None, reason=None):
    from app.models.strategy import Strategy
    s = Strategy(id=sid, name=f"风控策略{sid}", allocation_config={"512480": 1.0},
                 strategy_type="auto", strategy_source="auto_generated",
                 auto_strategy_status=status, status="active", initial_capital=initial,
                 paused_date=paused_date, paused_cooldown_days=cooldown, paused_reason=reason)
    db.add(s)
    db.commit()
    return s


def seed_snapshots(db, sid, pairs):
    """pairs: [(trade_date, total_asset), ...] 升序写入"""
    from app.models.portfolio import PortfolioSnapshot
    for i, (d, asset) in enumerate(pairs):
        prev = pairs[i - 1][1] if i else asset
        db.add(PortfolioSnapshot(
            strategy_id=sid, trade_date=d, total_asset=asset, cash=0.0, market_value=asset,
            profit=asset - 1000000.0, profit_pct=(asset / 1000000.0 - 1) * 100,
        ))
    db.commit()


class TestCircuitBreakerSemantics(unittest.TestCase):
    """单日亏损用日环比、累计亏损用 total_loss_threshold"""

    def setUp(self):
        from app.services.risk_controller import get_risk_controller
        self.ctrl = get_risk_controller()
        self.db = make_db()

    def _run(self, sid=1):
        return self.ctrl.check_circuit_breaker(sid, self.db)

    def test_small_day_move_not_triggered_even_with_cumulative_loss(self):
        """累计亏损 6% 但当日仅 -0.2% → 不应触发（原实现会误判为单日亏损）"""
        seed_strategy(self.db, initial=1000000.0)
        seed_snapshots(self.db, 1, [
            (TODAY - timedelta(days=2), 942000.0),
            (TODAY - timedelta(days=1), 940000.0),
            (TODAY, 938000.0),          # 日环比 -0.21%，累计 -6.2%
        ])
        result = self._run()
        self.assertEqual(result["status"], "normal")

    def test_real_single_day_drop_triggers(self):
        seed_strategy(self.db, initial=1000000.0)
        seed_snapshots(self.db, 1, [
            (TODAY - timedelta(days=1), 1000000.0),
            (TODAY, 965000.0),          # 日环比 -3.5%
        ])
        result = self._run()
        self.assertEqual(result["status"], "triggered")
        self.assertEqual(result["type"], "single_day_loss")
        self.assertIn("单日亏损-3.50%", result["reason"])
        self.assertEqual(result["cooldown_days"], 3)

    def test_total_loss_triggers_emergency_stop(self):
        seed_strategy(self.db, initial=1000000.0)
        seed_snapshots(self.db, 1, [
            (TODAY - timedelta(days=1), 900000.0),
            (TODAY, 890000.0),          # 累计 -11%（日环比 -1.1%）
        ])
        result = self._run()
        self.assertEqual(result["status"], "triggered")
        self.assertEqual(result["type"], "total_loss")
        self.assertEqual(result["action"], "emergency_stop")
        self.assertEqual(result["cooldown_days"], 7)

    def test_insufficient_data_normal(self):
        seed_strategy(self.db)
        result = self._run()
        self.assertEqual(result["status"], "normal")


class TestPauseWritesCooldown(unittest.TestCase):
    """风控暂停时写入冷却天数，供到期自动恢复"""

    def test_pause_sets_cooldown(self):
        from app.services.risk_controller import get_risk_controller
        db = make_db()
        ctrl = get_risk_controller()
        seed_strategy(db, initial=1000000.0)
        seed_snapshots(db, 1, [
            (TODAY - timedelta(days=1), 1000000.0),
            (TODAY, 960000.0),          # 日环比 -4%
        ])
        self.assertTrue(ctrl.should_pause_strategy(1, db))
        from app.models.strategy import Strategy
        s = db.query(Strategy).filter(Strategy.id == 1).first()
        self.assertEqual(s.auto_strategy_status, "paused")
        self.assertEqual(s.paused_cooldown_days, 3)
        self.assertIn("单日亏损", s.paused_reason)


class TestCooldownAutoResume(unittest.TestCase):
    """冷却到期自动恢复（仅风控暂停）"""

    def setUp(self):
        from app.services.risk_controller import get_risk_controller
        self.ctrl = get_risk_controller()
        self.db = make_db()

    def _status(self, sid=1):
        from app.models.strategy import Strategy
        s = self.db.query(Strategy).filter(Strategy.id == sid).first()
        return s.auto_strategy_status, s.paused_cooldown_days, s.paused_date

    def test_expired_cooldown_resumes(self):
        seed_strategy(self.db, status="paused", paused_date=TODAY - timedelta(days=4),
                      cooldown=3, reason="单日亏损-4%")
        resumed = self.ctrl.resume_if_cooldown_expired(self.db)
        self.assertEqual([r["strategy_id"] for r in resumed], [1])
        status, cooldown, paused_date = self._status()
        self.assertEqual(status, "running")
        self.assertIsNone(cooldown)
        self.assertIsNone(paused_date)

    def test_unexpired_cooldown_stays_paused(self):
        seed_strategy(self.db, status="paused", paused_date=TODAY - timedelta(days=1), cooldown=3)
        self.assertEqual(self.ctrl.resume_if_cooldown_expired(self.db), [])
        self.assertEqual(self._status()[0], "paused")

    def test_manual_pause_without_cooldown_never_auto_resumes(self):
        """LLM/人工暂停（无冷却天数）不自动恢复"""
        seed_strategy(self.db, status="paused", paused_date=TODAY - timedelta(days=30),
                      cooldown=None, reason="熔断触发：单日亏损-3.06%")
        self.assertEqual(self.ctrl.resume_if_cooldown_expired(self.db), [])
        self.assertEqual(self._status()[0], "paused")

    def test_running_strategy_untouched(self):
        seed_strategy(self.db, status="running")
        self.assertEqual(self.ctrl.resume_if_cooldown_expired(self.db), [])


if __name__ == "__main__":
    unittest.main()