"""规则策略化与快照落库测试"""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from app.models.strategy import Strategy, RuleSnapshot
from app.models.auto_strategy_log import AutoStrategyLog
from app.models.etf import ETFDailyIndicator


def make_db():
    """内存 SQLite + 全表 schema"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def make_log(db, strategy_id, log_date, regime, allocation, action="rebalance"):
    db.add(AutoStrategyLog(
        strategy_id=strategy_id, log_date=log_date, status="success",
        action_type="analyzed",
        analysis_result={"market_regime": regime, "suggested_action": action,
                         "suggested_allocation": allocation},
    ))


def seed_training_logs(db):
    """策略1(股票池)与策略2(债券池)各12条不同regime日志"""
    db.add(Strategy(name="股票策略", allocation_config={"510300": 1.0}))
    db.add(Strategy(name="债券策略", allocation_config={"511010": 1.0}))
    db.commit()
    start = date.today() - timedelta(days=30)
    for i in range(12):
        d = start + timedelta(days=i)
        make_log(db, 1, d, "bull_strong", {"510300": 0.9, "518880": 0.1})
        make_log(db, 2, d, "bull_strong", {"511010": 1.0})
    db.commit()


class TestTrainByStrategy(unittest.TestCase):
    """RuleTrainer.train(strategy_id) 按策略过滤"""

    def test_strategy_scope_only_contains_own_logs(self):
        from app.services.rule_trainer import RuleTrainer
        db = make_db()
        seed_training_logs(db)
        rules = RuleTrainer().train(db, days=90, strategy_id=2)
        self.assertEqual(rules["scope"], "strategy:2")
        rule = rules["regime_rules"]["bull_strong"]
        self.assertEqual(rule["sample_count"], 12)
        # 只含债券策略自己的标的，无股票/黄金
        self.assertEqual(set(rule["avg_allocation"].keys()), {"511010"})

    def test_global_scope_mixes_all(self):
        from app.services.rule_trainer import RuleTrainer
        db = make_db()
        seed_training_logs(db)
        rules = RuleTrainer().train(db, days=90, strategy_id=None)
        self.assertEqual(rules["scope"], "global")
        rule = rules["regime_rules"]["bull_strong"]
        self.assertEqual(rule["sample_count"], 24)
        self.assertIn("510300", rule["avg_allocation"])
        self.assertIn("511010", rule["avg_allocation"])


class TestAllocationPoolFilter(unittest.TestCase):
    """compute_daily_allocation 输出收敛到策略标的池"""

    def setUp(self):
        from app.services.rule_engine import RuleEngine
        self.engine = RuleEngine()
        self.db = make_db()
        seed_training_logs(self.db)
        td = date.today()
        for code in ("510300", "511010", "518880"):
            self.db.add(ETFDailyIndicator(
                etf_code=code, trade_date=td, composite_score=60.0,
                volatility_20d=10.0, momentum_5d=1.0, momentum_20d=2.0,
            ))
        self.db.commit()
        self.td = td

    def test_ai_rules_filtered_to_pool(self):
        """AI规则含池外标的时，输出必须收敛到池内并归一化"""
        with patch.object(self.engine, "_compute_regime", return_value="bull_strong"), \
             patch.object(self.engine, "_ensure_trained_rules", return_value={
                 "scope": "global",
                 "regime_rules": {"bull_strong": {
                     "avg_allocation": {"510300": 0.5, "518880": 0.3, "511010": 0.2},
                     "sample_count": 20, "typical_action": "hold"}},
             }):
            alloc = self.engine.compute_daily_allocation(
                self.td, self.db, {"511010": 1.0}, strategy_id=2)
        self.assertEqual(set(alloc.keys()), {"511010"})
        self.assertAlmostEqual(sum(alloc.values()), 1.0, places=4)

    def test_fallback_to_deterministic_when_no_ai_rules(self):
        """无AI规则 → 确定性规则，且收敛到策略池"""
        with patch.object(self.engine, "_compute_regime", return_value="bull_strong"), \
             patch.object(self.engine, "_ensure_trained_rules", return_value=None):
            alloc = self.engine.compute_daily_allocation(
                self.td, self.db, {"511010": 1.0}, strategy_id=2)
        self.assertTrue(set(alloc.keys()).issubset({"511010"}))
        self.assertAlmostEqual(sum(alloc.values()), 1.0, places=4)

    def test_final_fallback_to_static_allocation(self):
        """AI与确定性规则均无可用标的 → 静态配置兜底"""
        self.db.query(ETFDailyIndicator).delete()
        self.db.add(ETFDailyIndicator(
            etf_code="510300", trade_date=self.td, composite_score=60.0,
            volatility_20d=10.0, momentum_5d=1.0, momentum_20d=2.0,
        ))
        self.db.commit()
        with patch.object(self.engine, "_compute_regime", return_value="bull_strong"), \
             patch.object(self.engine, "_ensure_trained_rules", return_value=None):
            alloc = self.engine.compute_daily_allocation(
                self.td, self.db, {"511010": 1.0}, strategy_id=2)
        # 债券池策略：确定性规则无池内标的可用 → 静态配置
        self.assertEqual(alloc, {"511010": 1.0})


class TestRuleSnapshot(unittest.TestCase):
    """规则快照读取与新鲜度"""

    def setUp(self):
        from app.services.rule_engine import RuleEngine
        self.engine = RuleEngine()
        self.db = make_db()
        seed_training_logs(self.db)

    def _valid_rules(self, scope):
        return {"scope": scope, "regime_rules": {"neutral": {"avg_allocation": {"510300": 1.0},
                "sample_count": 5, "typical_action": "hold"}},
                "training_period": {"days": 30, "start": "2026-08-01", "end": "2026-08-30"}}

    def test_fresh_snapshot_used_without_retrain(self):
        """有新鲜快照时不现算"""
        self.db.add(RuleSnapshot(strategy_id=1, snapshot=self._valid_rules("strategy:1"),
                                 source="weekly_review", days_covered=30))
        self.db.commit()
        with patch("app.services.rule_trainer.RuleTrainer.train") as mock_train:
            rules = self.engine._ensure_trained_rules(self.db, strategy_id=1)
        mock_train.assert_not_called()
        self.assertEqual(rules["scope"], "strategy:1")

    def test_stale_snapshot_recomputes_and_persists(self):
        """超7天快照视为陈旧，现场重算并落新快照"""
        stale = self._valid_rules("global")
        self.db.add(RuleSnapshot(strategy_id=None, snapshot=stale, source="manual", days_covered=30,
                                 created_at=datetime.utcnow() - timedelta(days=8)))
        self.db.commit()
        with patch("app.services.rule_trainer.RuleTrainer.train",
                   return_value=self._valid_rules("global")) as mock_train:
            rules = self.engine._ensure_trained_rules(self.db, strategy_id=None)
        mock_train.assert_called_once()
        self.assertIsNotNone(rules)
        newest = self.db.query(RuleSnapshot).order_by(RuleSnapshot.created_at.desc()).first()
        self.assertIsNotNone(newest)
        self.assertGreater(newest.created_at, datetime.utcnow() - timedelta(days=1))

    def test_strategy_insufficient_falls_back_to_global(self):
        """策略规则数据不足 → 回退全局规则"""
        with patch("app.services.rule_trainer.RuleTrainer.train") as mock_train:
            def _train(db, days=90, strategy_id=None):
                if strategy_id is not None:
                    return {"scope": f"strategy:{strategy_id}", "regime_rules": {},
                            "training_period": {"days": 2}}
                return self._valid_rules("global")
            mock_train.side_effect = _train
            rules = self.engine._ensure_trained_rules(self.db, strategy_id=3)
        self.assertEqual(rules["scope"], "global")


if __name__ == "__main__":
    unittest.main()
