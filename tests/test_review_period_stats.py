"""4.3 复盘统计口径：期间收益率、skipped/failed 分账、全窗口回撤"""

import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
# create_all 只建「已导入模型」的表，故先导入全部模型（与 init_db 口径一致）
from app.models import (  # noqa: F401
    allocation_suggestion, auto_strategy_log, chat, etf, experience, factor_performance,
    market_regime, pipeline_checkpoint, portfolio, sentiment, stock_fundamental, strategy,
    sw_industry, system_config, task_log,
)
from app.models.auto_strategy_log import AutoStrategyLog
from app.models.portfolio import PortfolioSnapshot
from app.services.review_service import ReviewService

TODAY = date.today()
START = TODAY - timedelta(days=6)


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_strategy(db, sid=1):
    from app.models.strategy import Strategy
    db.add(Strategy(id=sid, name=f"复盘策略{sid}", allocation_config={"510300": 1.0},
                    strategy_type="auto", strategy_source="auto_generated",
                    auto_strategy_status="running", status="active",
                    initial_capital=1000000.0))
    db.commit()


def seed_assets(db, assets, sid=1, base=TODAY):
    """按日期升序写入净值序列（assets[0] 最早）"""
    for i, asset in enumerate(assets):
        db.add(PortfolioSnapshot(
            strategy_id=sid, trade_date=base - timedelta(days=len(assets) - 1 - i),
            total_asset=asset, cash=0.0, market_value=asset or 0.0,
            profit=(asset - 1000000.0) if asset else 0.0,
            profit_pct=((asset / 1000000.0 - 1) * 100) if asset else 0.0,
        ))
    db.commit()


def seed_logs(db, statuses, sid=1):
    """statuses 按日期升序（最后一个最新）"""
    for i, st in enumerate(statuses):
        db.add(AutoStrategyLog(strategy_id=sid, log_date=START + timedelta(days=i),
                               status=st, action_type="analyzed"))
    db.commit()


def snap(assets, base=None):
    """把资产值包成快照对象，assets[0] 为最早一天（倒序传 `[::-1]` 即 DB 的倒序口径）"""
    base = base or (TODAY - timedelta(days=len(assets) + 1))
    return [PortfolioSnapshot(strategy_id=1, trade_date=base + timedelta(days=i),
                              total_asset=a) for i, a in enumerate(assets)]


class TestPeriodReturns(unittest.TestCase):
    """`_period_returns` 用解析期望值锁口径"""

    def test_analytic_values(self):
        result = ReviewService._period_returns(snap([100000, 102000, 101000]))
        self.assertEqual(result["period_return"], 1.0)          # (101000-100000)/100000
        self.assertEqual(result["return_days"], 2)
        self.assertAlmostEqual(result["daily_avg_return"], 0.5098, places=4)
        self.assertEqual(result["max_loss"], -0.98)             # (101000-102000)/102000

    def test_insensitive_to_snapshot_order(self):
        """DB 查询不保证顺序，升序/倒序必须同结果"""
        assets = [100000, 102000, 101000]
        self.assertEqual(ReviewService._period_returns(snap(assets)),
                         ReviewService._period_returns(snap(assets)[::-1]))

    def test_flat_assets_is_zero_not_cumulative_level(self):
        """横盘两周：期间收益 0，而不是「成立以来累计水位」（旧口径把 profit_pct 求平均）"""
        snapshots = snap([1200000] * 5)
        for i, s in enumerate(snapshots):
            s.profit_pct = 20.0 + i        # 旧口径会平均出 ~22%
        result = ReviewService._period_returns(snapshots)
        self.assertEqual(result["period_return"], 0.0)
        self.assertEqual(result["daily_avg_return"], 0.0)
        self.assertEqual(result["max_loss"], 0.0)

    def test_single_or_empty(self):
        self.assertEqual(ReviewService._period_returns([]),
                         {"period_return": 0.0, "daily_avg_return": 0.0,
                          "return_days": 0, "max_loss": 0.0})
        self.assertEqual(ReviewService._period_returns(snap([100000]))["return_days"], 0)

    def test_missing_assets_are_skipped(self):
        """total_asset 为 None/0 的脏快照不参与，也不该让除零把整段统计打崩"""
        result = ReviewService._period_returns(snap([None, 100000, 0, 102000]))
        self.assertEqual(result["period_return"], 2.0)
        self.assertEqual(result["return_days"], 1)


class TestCollectPeriodData(unittest.TestCase):

    def setUp(self):
        self.db = make_db()
        seed_strategy(self.db)

    def tearDown(self):
        self.db.close()

    def test_failed_and_skipped_are_separate_counters(self):
        seed_logs(self.db, ["success", "success", "failed", "skipped"])
        data = ReviewService()._collect_period_data(1, START, TODAY, self.db)
        self.assertEqual(data["total_count"], 4)
        self.assertEqual(data["success_count"], 2)
        self.assertEqual(data["failure_count"], 1)     # 旧口径 total-success = 2
        self.assertEqual(data["skipped_count"], 1)

    def test_failure_cases_exclude_skipped(self):
        seed_logs(self.db, ["failed", "skipped"])
        data = ReviewService()._collect_period_data(1, START, TODAY, self.db)
        self.assertEqual(len(data["failure_cases"]), 1)

    def test_period_return_matches_assets(self):
        seed_assets(self.db, [1000000, 1010000, 1030000])
        data = ReviewService()._collect_period_data(1, TODAY - timedelta(days=6), TODAY, self.db)
        self.assertEqual(data["period_return"], 3.0)
        self.assertEqual(data["return_days"], 2)

    def test_report_statistics_keys(self):
        seed_assets(self.db, [1000000, 950000])
        seed_logs(self.db, ["success", "failed", "skipped"])
        report = ReviewService().get_review_report(1, "weekly", self.db)
        stats = report["statistics"]
        self.assertNotIn("avg_return", stats)
        self.assertEqual(stats["period_return"], -5.0)
        self.assertEqual(stats["daily_avg_return"], -5.0)
        self.assertEqual(stats["max_loss"], -5.0)
        self.assertEqual(stats["failure_count"], 1)
        self.assertEqual(stats["skipped_count"], 1)
        self.assertIn("区间收益", report["summary"])

    def test_llm_prompt_uses_period_fields(self):
        """跑通 format(**period_data) 的键齐全（缺键会在复盘时 KeyError）"""
        seed_assets(self.db, [1000000, 1010000, 1005000])
        seed_logs(self.db, ["success", "skipped"])
        data = ReviewService()._collect_period_data(1, START, TODAY, self.db)
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="[]"))]
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        svc = ReviewService()
        svc.llm_client = client
        self.assertEqual(svc._generate_experiences_with_llm(data), [])
        prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("区间收益率: 0.5%", prompt)
        self.assertIn("跳过次数: 1", prompt)


class TestConsecutiveFailureSplit(unittest.TestCase):

    def setUp(self):
        self.db = make_db()
        seed_strategy(self.db)
        # detect_anomalies 在快照不足 2 个时直接返回空，状态分账用例也得有净值序列
        seed_assets(self.db, [1000000, 1000000])

    def tearDown(self):
        self.db.close()

    def _anomaly(self, statuses):
        seed_logs(self.db, statuses)
        found = [a for a in ReviewService().detect_anomalies(1, self.db)
                 if a["type"] == "consecutive_failure"]
        self.db.query(AutoStrategyLog).delete()
        self.db.commit()
        return found[0] if found else None

    def test_three_failures_trigger(self):
        anomaly = self._anomaly(["success", "failed", "failed", "failed"])
        self.assertEqual(anomaly["count"], 3)

    def test_skipped_do_not_count_as_failure(self):
        """旧口径 status != success 把 3 次跳过读成「连续 3 次失败」→ 误报"""
        self.assertIsNone(self._anomaly(["skipped", "skipped", "skipped"]))

    def test_skipped_between_failures_tallied_separately(self):
        anomaly = self._anomaly(["failed", "skipped", "failed", "skipped", "failed"])
        self.assertEqual(anomaly["count"], 3)
        self.assertEqual(anomaly["skipped_count"], 2)
        self.assertIn("跳过2次", anomaly["message"])

    def test_success_breaks_streak(self):
        self.assertIsNone(self._anomaly(["failed", "failed", "failed", "success", "failed"]))


class TestDrawdownSpike(unittest.TestCase):

    def setUp(self):
        self.db = make_db()
        seed_strategy(self.db)

    def tearDown(self):
        self.db.close()

    def test_sign_convention_is_negative(self):
        """阈值与文案都按「跌幅为负」写；旧实现返回恒正 → 告警分支永不进入

        `_calculate_recent_drawdown` 沿用 DB 的**倒序**口径（[0] 即最新），故传 `[::-1]`。
        """
        svc = ReviewService()
        self.assertEqual(svc._calculate_recent_drawdown(snap([1000000, 1100000])[::-1]), 0.0)
        self.assertAlmostEqual(svc._calculate_recent_drawdown(snap([1100000, 1000000])[::-1]),
                               -1 / 11, places=6)

    def test_peak_older_than_five_snapshots_still_detected(self):
        """高点在 6 个快照之前：只扫最近 5 个会读成没回撤"""
        assets = [1000000] * 6 + [990000, 980000, 970000, 960000, 950000, 900000]
        seed_assets(self.db, assets)
        anomalies = ReviewService().detect_anomalies(1, self.db)
        spike = [a for a in anomalies if a["type"] == "drawdown_spike"]
        self.assertEqual(len(spike), 1)
        self.assertEqual(spike[0]["drawdown_pct"], -10.0)

    def test_small_dip_below_threshold_not_flagged(self):
        seed_assets(self.db, [1000000, 995000, 990000, 985000, 980000, 975000])
        self.assertNotIn("drawdown_spike",
                         [a["type"] for a in ReviewService().detect_anomalies(1, self.db)])

    def test_suggestion_now_reachable(self):
        """回撤突增 → 建议暂停（旧实现因符号问题这条建议从未产出过）"""
        seed_assets(self.db, [1000000] * 4 + [940000, 900000])
        result = ReviewService().suggest_parameter_adjustments(1, self.db)
        suggestions = result["suggestions"] if isinstance(result, dict) else result
        self.assertIn("auto_strategy_status", [s["parameter"] for s in suggestions])


if __name__ == "__main__":
    unittest.main()
