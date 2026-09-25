"""舆情接入决策测试：情绪极端判定、舆情注入辩论、门槛加严、条件触发复核留痕"""
import unittest
from datetime import date
from unittest.mock import patch

DAY = date(2026, 9, 25)


def make_pair():
    """同库双会话工厂（被测函数会自行开关 Session）"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def seed_strategy(db, pool=None, sid=1):
    from app.models.strategy import Strategy
    s = Strategy(id=sid, name=f"情绪策略{sid}", allocation_config=pool or {"512480": 1.0},
                 strategy_type="auto", strategy_source="auto_generated",
                 auto_strategy_status="running", status="active")
    db.add(s)
    db.commit()
    return s


def seed_indicator(db, code, score=60.0, day=DAY):
    from app.models.etf import ETFDailyIndicator
    db.add(ETFDailyIndicator(etf_code=code, trade_date=day, composite_score=score,
                             rank_in_market=1, momentum_5d=1.0, momentum_20d=2.0,
                             trend_strength=2, volatility_20d=10.0, vol_ratio=1.0,
                             ma5=1.0, ma10=1.0, ma20=1.0))
    db.commit()


def seed_sentiment(db, score, label="negative", related=None, day=DAY, title="测试新闻"):
    from app.models.sentiment import SentimentData
    db.add(SentimentData(data_date=day, source="eastmoney", data_type="news",
                         title=title, sentiment_score=score, sentiment_label=label,
                         related_etfs=related))
    db.commit()


class TestExtremeDetection(unittest.TestCase):
    """SentimentService.evaluate_extreme：市场级 + 标的级"""

    def setUp(self):
        from app.services.sentiment_service import SentimentService
        self.svc = SentimentService()
        _, self.factory = make_pair()
        self.db = self.factory()
        seed_strategy(self.db)
        seed_indicator(self.db, "512480")

    def _seed_market(self, score):
        for i in range(5):
            seed_sentiment(self.db, score, title=f"新闻{i}")

    def test_market_negative_extreme(self):
        self._seed_market(-0.6)
        guard = self.svc.evaluate_extreme(self.db, 1)
        self.assertTrue(guard["extreme"])
        self.assertIn("市场情绪负面极端", guard["reasons"][0])
        self.assertEqual(guard["date"], DAY.isoformat())

    def test_market_normal_not_extreme(self):
        self._seed_market(-0.1)
        guard = self.svc.evaluate_extreme(self.db, 1)
        self.assertFalse(guard["extreme"])

    def test_related_negative_count_triggers(self):
        self._seed_market(-0.1)                      # 市场不极端
        for i in range(3):
            seed_sentiment(self.db, -0.5, related=["512480"], title=f"持仓负面{i}")
        guard = self.svc.evaluate_extreme(self.db, 1)
        self.assertTrue(guard["extreme"])
        self.assertEqual(guard["related_negative"], 3)
        self.assertIn("涉本策略标的负面舆情", guard["reasons"][0])

    def test_related_but_other_etf_not_counted(self):
        self._seed_market(-0.1)
        for i in range(5):
            seed_sentiment(self.db, -0.5, related=["159915"], title=f"非池内负面{i}")
        guard = self.svc.evaluate_extreme(self.db, 1)
        self.assertFalse(guard["extreme"])
        self.assertEqual(guard["related_negative"], 0)

    def test_no_data_degrades(self):
        guard = self.svc.evaluate_extreme(self.db, 1)
        self.assertFalse(guard["extreme"])
        self.assertIsNone(guard["market_score"])


class TestSentimentContext(unittest.TestCase):
    """舆情材料渲染"""

    def test_format_with_data(self):
        from app.services.strategy_evidence_service import format_sentiment_context
        text = format_sentiment_context({
            "market_total": 200, "market_avg_score": 0.05,
            "total": 12, "window_days": 7, "avg_score": -0.35, "as_of": DAY.isoformat(),
            "recent": [{"date": DAY.isoformat(), "title": "某板块大幅下挫", "etf_codes": ["512480"]}],
            "market_recent": [{"date": DAY.isoformat(), "title": "市场头条A"}],
        })
        self.assertIn("市场舆情：近7日 200 条", text)
        self.assertIn("涉本策略标的：12 条", text)
        self.assertIn("偏空", text)          # 标的级 -0.35
        self.assertIn("512480", text)
        self.assertIn("不作为追涨依据", text)

    def test_format_market_only(self):
        """线上真实场景：无 ETF 关联，仅市场级情绪也要能进辩论材料"""
        from app.services.strategy_evidence_service import format_sentiment_context
        text = format_sentiment_context({
            "market_total": 212, "market_avg_score": 0.211, "total": 0, "avg_score": None,
            "window_days": 7, "as_of": DAY.isoformat(), "recent": [],
            "market_recent": [{"date": DAY.isoformat(), "title": "今日热门概念关键词"}],
        })
        self.assertIn("市场舆情：近7日 212 条", text)
        self.assertIn("偏多", text)
        self.assertIn("未建立 ETF 关联", text)

    def test_format_without_data(self):
        from app.services.strategy_evidence_service import format_sentiment_context
        self.assertIn("无舆情参考", format_sentiment_context(None))
        self.assertIn("无舆情参考", format_sentiment_context({"total": 0}))

    def test_format_without_related_etfs(self):
        from app.services.strategy_evidence_service import format_sentiment_context
        text = format_sentiment_context({"market_total": 5, "market_avg_score": 0.1, "total": 0,
                                         "window_days": 7, "as_of": DAY.isoformat(), "recent": []})
        self.assertIn("涉本策略标的：无", text)


class TestRotationSentimentInjection(unittest.TestCase):
    """轮动评估：舆情注入辩论 + 门槛可覆盖"""

    def setUp(self):
        from app.services.rotation_service import RotationService
        _, self.factory = make_pair()
        self.db = self.factory()
        self.svc = RotationService()
        self.s = seed_strategy(self.db, pool={"512480": 1.0})
        seed_indicator(self.db, "512480", score=70)   # 持仓
        seed_indicator(self.db, "159915", score=74)   # 候选，仅领先 4 分（常规门槛 5 不通）

    def _run(self, **kwargs):
        captured = {}

        def fake_debate(holdings, candidates, rule_signal=None, sector_context="", sentiment_context=""):
            captured["sentiment"] = sentiment_context
            return {"decision": "hold", "final_swaps": [], "summary": "维持持仓"}

        with patch.object(self.svc, "_run_debate", side_effect=fake_debate), \
             patch.object(self.svc, "_build_rule_signal", return_value=None), \
             patch.object(self.svc, "_build_sentiment_context", return_value="市场舆情：偏空"), \
             patch.object(self.svc, "_attach_value_signals"), \
             patch("app.services.sector_selection_service.SectorSelectionService.get_mode",
                   return_value="off"):
            plan = self.svc.evaluate_rotation(self.s.id, DAY, self.db, **kwargs)
        return plan, captured

    def test_sentiment_context_passed_to_debate(self):
        """常规门槛下差距 4 分不进辩论；门槛加严后进辩论且携带舆情材料"""
        plan_default, captured_default = self._run()
        self.assertEqual(plan_default["gap_threshold"], 5.0)
        self.assertNotIn("sentiment", captured_default)
        plan, captured = self._run(gap_threshold=3.0)
        self.assertEqual(captured.get("sentiment"), "市场舆情：偏空")

    def test_gap_threshold_override_changes_gate(self):
        """差距 4 分：常规门槛直接 hold（未进辩论），加严门槛（3 分）进入辩论"""
        plan_default, _ = self._run()
        self.assertEqual(plan_default["action"], "hold")
        self.assertIn("差距不足", plan_default["reason"])
        plan_condition, captured = self._run(gap_threshold=3.0)
        self.assertEqual(plan_condition["gap_threshold"], 3.0)
        self.assertEqual(plan_condition["action"], "hold")
        self.assertIn("维持持仓", plan_condition["reason"])   # 已进辩论（辩论裁决维持）
        self.assertEqual(captured.get("sentiment"), "市场舆情：偏空")


class TestConditionReviewJob(unittest.TestCase):
    """调度器：情绪极端 → 条件触发复核并留痕"""

    def setUp(self):
        from app.tasks import scheduler as sch
        self.sch = sch
        _, self.factory = make_pair()
        db = self.factory()
        seed_strategy(db)
        seed_indicator(db, "512480")
        db.close()

    def _run(self, extreme, enabled=True):
        calls = []

        def fake_evaluate(strategy_id, plan_date, db, gap_threshold=None):
            calls.append({"strategy_id": strategy_id, "threshold": gap_threshold})
            return {"action": "hold", "reason": "条件复核维持持仓", "gap_threshold": gap_threshold,
                    "sector_meta": {"mode": "off"}}

        from app.config import get_settings
        settings = get_settings()
        old_enabled = settings.sentiment_review_enabled
        settings.sentiment_review_enabled = enabled
        with patch("app.db.database.SessionLocal", self.factory), \
             patch("app.services.sentiment_service.SentimentService.evaluate_extreme",
                   return_value={"extreme": extreme, "reasons": ["市场情绪负面极端（均分 -0.6）"] if extreme else [],
                                 "market_score": -0.6 if extreme else -0.1, "related_negative": 0,
                                 "date": DAY.isoformat(), "total": 10}), \
             patch("app.services.rotation_service.RotationService.evaluate_rotation",
                   side_effect=fake_evaluate):
            try:
                self.sch._maybe_trigger_sentiment_review()
            finally:
                settings.sentiment_review_enabled = old_enabled
        return calls

    def test_extreme_triggers_review_with_tightened_threshold(self):
        calls = self._run(extreme=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["threshold"], 3.0)

        db = self.factory()
        from app.models.auto_strategy_log import AutoStrategyLog
        from app.models.task_log import TaskExecutionLog
        log = db.query(AutoStrategyLog).filter_by(action_type="analyzed").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.analysis_result["trigger"], "condition")
        self.assertEqual(log.analysis_result["source"], "sentiment_condition")
        self.assertIn("情绪条件触发", log.analysis_result["action_reason"])
        task_log = db.query(TaskExecutionLog).filter_by(task_name="sentiment_condition_review").first()
        self.assertIsNotNone(task_log)
        self.assertEqual(task_log.result_summary["triggered"], 1)
        db.close()

    def test_not_extreme_skips(self):
        calls = self._run(extreme=False)
        self.assertEqual(calls, [])
        db = self.factory()
        from app.models.auto_strategy_log import AutoStrategyLog
        self.assertIsNone(db.query(AutoStrategyLog).filter_by(action_type="analyzed").first())
        db.close()

    def test_disabled_switch_skips(self):
        calls = self._run(extreme=True, enabled=False)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()