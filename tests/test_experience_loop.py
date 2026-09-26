"""经验闭环测试：应用记录 → 效果评估 → 有效性 → 生命周期，以及复盘/异常覆盖口径"""
import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from tests.test_value_model import make_db

TODAY = date.today()


def seed_strategy(db, sid=1, status="running"):
    from app.models.strategy import Strategy
    s = Strategy(id=sid, name=f"经验策略{sid}", allocation_config={"512480": 1.0},
                 strategy_type="auto", strategy_source="auto_generated",
                 auto_strategy_status=status, status="active", initial_capital=1000000.0)
    db.add(s)
    db.commit()
    return s


def seed_experience(db, sid=1, title="高波动追涨教训", exp_type="failure",
                    tags=None, result="negative", weight=1.0, generated=None):
    from app.models.experience import Experience
    gen = generated or TODAY - timedelta(days=1)
    exp = Experience(
        strategy_id=sid, experience_type=exp_type, scenario_tags=tags or ["高波动"],
        title=title, description="描述", result=result, key_insight="关键洞察",
        generated_date=gen, expires_date=gen + timedelta(days=90), weight=weight,
        is_active=True,
    )
    db.add(exp)
    db.commit()
    return exp


def seed_snapshot_series(db, sid=1, days=25, start_asset=1000000.0, daily_return=0.002):
    """升序写入一段资产序列（默认每日 +0.2%）"""
    from app.models.portfolio import PortfolioSnapshot
    asset = start_asset
    for i in range(days):
        d = TODAY - timedelta(days=days - 1 - i)
        db.add(PortfolioSnapshot(
            strategy_id=sid, trade_date=d, total_asset=asset, cash=0.0, market_value=asset,
            profit=asset - 1000000.0, profit_pct=(asset / 1000000.0 - 1) * 100,
        ))
        asset *= (1 + daily_return)
    db.commit()


class TestRecordUsage(unittest.TestCase):
    """应用记录：显式 id / 兜底 / 当日幂等"""

    def setUp(self):
        from app.services.experience_manager import ExperienceManager
        self.mgr = ExperienceManager()
        self.db = make_db()
        seed_strategy(self.db)
        self.e1 = seed_experience(self.db, title="经验A", weight=1.0)
        self.e2 = seed_experience(self.db, title="经验B", weight=0.5)

    def test_explicit_ids(self):
        n = self.mgr.record_usage(1, self.db, experience_ids=[self.e1.id], decision={"action": "hold"})
        self.assertEqual(n, 1)
        from app.models.experience import ExperienceUsageRecord, Experience
        rec = self.db.query(ExperienceUsageRecord).first()
        self.assertEqual(rec.experience_id, self.e1.id)
        self.assertEqual(rec.decision_made["source"], "explicit")
        self.assertEqual(rec.decision_made["action"], "hold")
        self.assertEqual(self.db.query(Experience).filter(Experience.id == self.e1.id).first().application_count, 1)

    def test_same_day_idempotent(self):
        self.mgr.record_usage(1, self.db, experience_ids=[self.e1.id])
        n = self.mgr.record_usage(1, self.db, experience_ids=[self.e1.id])
        self.assertEqual(n, 0)

    def test_implicit_fallback_marks_source(self):
        n = self.mgr.record_usage(1, self.db, experience_ids=None)
        self.assertEqual(n, 2)          # 两条活跃经验兜底
        from app.models.experience import ExperienceUsageRecord
        sources = {r.decision_made["source"] for r in self.db.query(ExperienceUsageRecord).all()}
        self.assertEqual(sources, {"implicit"})

    def test_no_experience_returns_zero(self):
        empty = make_db()
        seed_strategy(empty)
        self.assertEqual(self.mgr.record_usage(1, empty, experience_ids=None), 0)


class TestFullExperienceLoop(unittest.TestCase):
    """完整闭环：3 次应用 → 评估为正 → 有效性评分 → 验证通过"""

    def setUp(self):
        from app.services.experience_manager import ExperienceManager
        self.mgr = ExperienceManager()
        self.db = make_db()
        seed_strategy(self.db)
        self.exp = seed_experience(self.db, generated=TODAY - timedelta(days=20))
        seed_snapshot_series(self.db, days=25, daily_return=0.002)

    def test_loop_produces_effectiveness(self):
        from app.models.experience import ExperienceUsageRecord, Experience

        # 三次应用（分布在 12/10/8 天前，保证各有 ≥5 个后续快照）
        for offset in (12, 10, 8):
            self.db.add(ExperienceUsageRecord(
                experience_id=self.exp.id, strategy_id=1,
                usage_date=TODAY - timedelta(days=offset),
                decision_made={"source": "explicit"},
            ))
        self.db.commit()
        self.exp.application_count = 3
        self.db.commit()

        self.mgr.evaluate_experience_usages(1, self.db)
        records = self.db.query(ExperienceUsageRecord).all()
        self.assertTrue(all(r.result == "positive" for r in records), [r.result for r in records])
        self.assertTrue(all(r.is_validated for r in records))

        self.mgr.update_experience_lifecycle(1, self.db)
        exp = self.db.query(Experience).filter(Experience.id == self.exp.id).first()
        self.assertTrue(exp.is_validated)
        self.assertEqual(exp.success_rate, 1.0)
        self.assertEqual(exp.effectiveness_score, 10.0)
        self.assertEqual(exp.success_count, 3)

    def test_weight_decay_and_expiry(self):
        from app.models.experience import Experience
        old = seed_experience(self.db, title="很旧的经验", generated=TODAY - timedelta(days=120))
        self.mgr.update_experience_lifecycle(1, self.db)
        exp = self.db.query(Experience).filter(Experience.id == old.id).first()
        self.assertFalse(exp.is_active)                     # 过期
        self.assertGreaterEqual(exp.weight, 0.3)            # 权重衰减有下限


class TestAutonomousUsageRecording(unittest.TestCase):
    """自动决策：从工具调用提取经验 id 并记录应用"""

    def setUp(self):
        from tests.test_value_model import make_db as _mk
        self.db = _mk()
        seed_strategy(self.db)
        self.e1 = seed_experience(self.db, title="经验A")
        self.e2 = seed_experience(self.db, title="经验B")

    def test_usage_recorded_from_tool_calls(self):
        from app.agent_core.loop import AgentResponse
        from app.tasks.scheduler import _record_autonomous_analysis
        from app.models.auto_strategy_log import AutoStrategyLog
        from app.models.experience import ExperienceUsageRecord

        result = AgentResponse(content="参考经验后维持持仓", tool_calls_made=[
            {"tool": "get_experience_insights", "arguments": {"strategy_id": 1},
             "result": {"top_experiences": [{"id": self.e1.id}, {"id": self.e2.id}]}},
        ])
        _record_autonomous_analysis(result, self.db)

        records = self.db.query(ExperienceUsageRecord).all()
        self.assertEqual({r.experience_id for r in records}, {self.e1.id, self.e2.id})
        log = self.db.query(AutoStrategyLog).filter_by(strategy_id=1, action_type="analyzed").first()
        self.assertEqual(log.analysis_result["evidence"]["experience_used"], 2)

    def test_usage_falls_back_when_no_tool_call(self):
        from app.agent_core.loop import AgentResponse
        from app.tasks.scheduler import _record_autonomous_analysis
        from app.models.experience import ExperienceUsageRecord

        _record_autonomous_analysis(AgentResponse(content="未查阅经验", tool_calls_made=[]), self.db)
        records = self.db.query(ExperienceUsageRecord).all()
        self.assertEqual(len(records), 2)                       # 兜底记录活跃经验
        self.assertEqual(records[0].decision_made["source"], "implicit")


class TestReviewCoverage(unittest.TestCase):
    """复盘覆盖暂停策略 + 异常检测口径"""

    def test_weekly_review_includes_paused_strategy(self):
        from app.tasks import scheduler as sch
        db = make_db()
        seed_strategy(db, sid=1, status="paused")
        called = []
        with patch("app.db.database.SessionLocal", return_value=db), \
             patch("app.services.review_service.ReviewService.trigger_review",
                   side_effect=lambda sid, rtype, d: called.append((sid, rtype)) or {"experiences_generated": 0}):
            sch._job_weekly_review()
        self.assertEqual(called, [(1, "weekly")])

    def test_anomaly_large_loss_uses_day_over_day(self):
        from app.services.review_service import ReviewService
        db = make_db()
        seed_strategy(db)
        # 累计 -6% 但每日仅 -0.2% → 不应判为大额亏损（原实现会误判）
        seed_snapshot_series(db, days=10, daily_return=-0.002)
        anomalies = ReviewService().detect_anomalies(1, db)
        self.assertNotIn("large_loss", [a["type"] for a in anomalies])

    def test_anomaly_large_loss_triggers_on_real_drop(self):
        from app.services.review_service import ReviewService
        from app.models.portfolio import PortfolioSnapshot
        db = make_db()
        seed_strategy(db)
        seed_snapshot_series(db, days=5, daily_return=0.0)
        # 最后一天真实下跌 -6%
        for snap in db.query(PortfolioSnapshot).all():
            snap.total_asset = 1000000.0
        db.query(PortfolioSnapshot).filter(PortfolioSnapshot.trade_date == TODAY).update({"total_asset": 940000.0})
        db.commit()
        anomalies = ReviewService().detect_anomalies(1, db)
        self.assertIn("large_loss", [a["type"] for a in anomalies])

    def test_anomaly_reviews_cover_paused(self):
        from app.tasks import scheduler as sch
        db = make_db()
        seed_strategy(db, sid=1, status="paused")
        called = []
        with patch("app.services.review_service.ReviewService.detect_anomalies",
                   return_value=[{"type": "large_loss", "message": "单日大幅亏损-6%", "date": TODAY.isoformat()}]), \
             patch("app.services.review_service.ReviewService.trigger_anomaly_review",
                   side_effect=lambda sid, a, d: called.append(sid) or {"corrective_experience": {"title": "x"}}):
            sch._run_anomaly_reviews(db)
        self.assertEqual(called, [1])

    def test_no_anomaly_no_llm_call(self):
        from app.tasks import scheduler as sch
        db = make_db()
        seed_strategy(db)
        with patch("app.services.review_service.ReviewService.detect_anomalies", return_value=[]), \
             patch("app.services.review_service.ReviewService.trigger_anomaly_review") as mock_review:
            sch._run_anomaly_reviews(db)
        mock_review.assert_not_called()


class TestSmartMatchToolJsonSafe(unittest.TestCase):
    """智能匹配工具返回纯字典（含 id），可 JSON 序列化"""

    def test_returns_plain_dicts(self):
        from app.tools.analysis_tools import smart_match_experiences
        db = make_db()
        seed_strategy(db)
        exp = seed_experience(db, tags=["高波动"])
        fake_match = [{
            "experience": exp, "scenario_similarity": 0.9, "adjusted_weight": 1.2,
            "tags_matched": ["高波动"], "match_type": "scenario_based",
        }]
        with patch("app.services.smart_experience_matcher.SmartExperienceMatcher."
                   "match_experiences_by_scenario", return_value=fake_match):
            result = smart_match_experiences(db, strategy_id=1)
        json.dumps(result)                      # ORM 对象必须已转纯字典
        self.assertEqual(result["total_matched"], 1)
        self.assertEqual(result["matched_experiences"][0]["id"], exp.id)
        self.assertEqual(result["matched_experiences"][0]["scenario_similarity"], 0.9)


if __name__ == "__main__":
    unittest.main()