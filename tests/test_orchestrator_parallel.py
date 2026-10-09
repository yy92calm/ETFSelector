"""Orchestrator 并行执行器：互不依赖的Agent并发跑，单点失败不抛穿"""
import threading
import time
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from app.agents.orchestrator import Orchestrator


def patched_factory():
    """给 _run_parallel 用的假 Session 工厂（不触达真实库）"""
    return MagicMock(side_effect=lambda: MagicMock())


class TestRunParallel(unittest.TestCase):
    def setUp(self):
        self.o = Orchestrator()
        factory = patched_factory()
        patcher = patch("app.agents.orchestrator.SessionLocal", factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sessions_created = factory

    def test_returns_keyed_by_task_name(self):
        out = self.o._run_parallel([
            ("a", lambda s: {"v": 1}),
            ("b", lambda s: {"v": 2}),
        ])
        self.assertEqual(out, {"a": {"v": 1}, "b": {"v": 2}})

    def test_tasks_run_concurrently(self):
        """4 个各睡 0.3s 的任务应远小于串行的 1.2s"""
        start = time.perf_counter()
        self.o._run_parallel([
            (f"t{i}", lambda s: time.sleep(0.3)) for i in range(4)
        ])
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 0.7, f"疑似串行执行，耗时 {elapsed:.2f}s")

    def test_each_task_gets_its_own_session(self):
        """Session 不可跨线程共享：每个任务必须拿到独立实例"""
        seen = []

        def grab(s):
            seen.append(s)
            return {}

        self.o._run_parallel([(f"t{i}", grab) for i in range(4)])
        self.assertEqual(len(seen), 4)
        self.assertEqual(len({id(x) for x in seen}), 4, "任务共用了同一个 Session")

    def test_exception_becomes_error_dict(self):
        def boom(s):
            raise RuntimeError("llm down")

        out = self.o._run_parallel([
            ("bad", boom),
            ("good", lambda s: {"ok": True}),
        ])
        self.assertEqual(out["good"], {"ok": True})
        self.assertIn("error", out["bad"])
        self.assertIn("llm down", out["bad"]["error"])

    def test_sessions_are_closed(self):
        sessions = []

        def spy(s):
            sessions.append(s)
            return {}

        self.o._run_parallel([("t1", spy), ("t2", spy)])
        for s in sessions:
            s.close.assert_called_once()

    def test_single_task_path_still_isolated(self):
        out = self.o._run_parallel([("only", lambda s: {"v": 7})])
        self.assertEqual(out, {"only": {"v": 7}})

    def test_runs_in_worker_threads(self):
        main = threading.current_thread().name
        names = []
        self.o._run_parallel([(f"t{i}", lambda s: names.append(threading.current_thread().name))
                              for i in range(3)])
        self.assertTrue(all(n != main for n in names), "任务应跑在工作线程")


class TestAnalyzePhases(unittest.TestCase):
    """analyze() 的阶段编排：并行批次产出仍完整进入汇总"""

    def setUp(self):
        self.o = Orchestrator()
        for name in ("technical_analyst", "sentiment_analyst", "macro_cycle",
                     "cross_asset", "volatility_regime", "bull_researcher",
                     "bear_researcher", "market_analyst", "theme_discovery",
                     "rebalance_timing"):
            setattr(self.o, name, MagicMock())
        self.o._ensure_fresh_data = MagicMock(return_value={
            "status": "fresh", "latest_date": "2026-09-04", "lag_days": 0})
        self.o._compute_lock_date = MagicMock(return_value=date(2026, 9, 4))

        patcher = patch("app.agents.orchestrator.SessionLocal", patched_factory())
        patcher.start()
        self.addCleanup(patcher.stop)

        self.db = MagicMock()
        strategy = MagicMock()
        strategy.allocation_config = {"510300": 0.5, "510500": 0.5}
        strategy.last_analysis_result = None
        strategy.last_auto_analysis_date = None
        self.db.query.return_value.filter.return_value.first.return_value = strategy
        self.strategy = strategy

        for attr, key in (("technical_analyst", "t"), ("sentiment_analyst", "s"),
                          ("macro_cycle", "m"), ("cross_asset", "c"),
                          ("volatility_regime", "v"), ("bull_researcher", "b"),
                          ("bear_researcher", "r"), ("theme_discovery", "th"),
                          ("rebalance_timing", "rb")):
            getattr(self.o, attr).analyze.return_value = {"tag": key}
        self.o.market_analyst.analyze.return_value = {
            "market_regime": "bull", "suggested_action": "hold"}
        patcher2 = patch("app.services.portfolio_service.get_portfolio_service")
        patcher2.start()
        self.addCleanup(patcher2.stop)

    def test_all_agents_called_once(self):
        self.o.analyze(1, date(2026, 9, 5), self.db)
        for attr in ("technical_analyst", "sentiment_analyst", "macro_cycle",
                     "cross_asset", "volatility_regime", "bull_researcher",
                     "bear_researcher", "market_analyst", "theme_discovery",
                     "rebalance_timing"):
            self.assertEqual(
                getattr(self.o, attr).analyze.call_count, 1, f"{attr} 调用次数异常")

    def test_combined_contains_every_report(self):
        result = self.o.analyze(1, date(2026, 9, 5), self.db)
        for field, tag in (("technical_report", "t"), ("sentiment_report", "s"),
                           ("macro_report", "m"), ("cross_asset_report", "c"),
                           ("volatility_report", "v"), ("bull_report", "b"),
                           ("bear_report", "r"), ("theme_report", "th"),
                           ("rebalance_timing", "rb")):
            self.assertEqual(result[field]["tag"], tag, f"{field} 内容错位")
        self.assertEqual(result["market_regime"], "bull")

    def test_debate_receives_stage1_reports(self):
        """多空辩论仍须拿到阶段1的技术/情绪报告与宏观/跨资产/波动率增强"""
        self.o.analyze(1, date(2026, 9, 5), self.db)
        args, kwargs = self.o.bull_researcher.analyze.call_args
        self.assertEqual(args[0]["tag"], "t")
        self.assertEqual(args[1]["tag"], "s")
        self.assertEqual(kwargs["macro_report"]["tag"], "m")
        self.assertEqual(kwargs["cross_asset_report"]["tag"], "c")
        self.assertEqual(kwargs["volatility_report"]["tag"], "v")

    def test_stage1_failures_do_not_abort_flow(self):
        self.o.technical_analyst.analyze.return_value = {"error": "数据不足"}
        self.o.macro_cycle.analyze.return_value = {"error": "无法判断"}
        result = self.o.analyze(1, date(2026, 9, 5), self.db)
        self.assertEqual(result["technical_report"]["error"], "数据不足")
        self.o.bull_researcher.analyze.assert_called_once()
        self.o.market_analyst.analyze.assert_called_once()

    def test_wall_time_beats_serial_sum(self):
        """每个Agent耗时0.2s，10个Agent并行批次应远快于串行2s"""
        for attr in ("technical_analyst", "sentiment_analyst", "macro_cycle",
                     "cross_asset", "volatility_regime", "bull_researcher",
                     "bear_researcher", "theme_discovery", "rebalance_timing"):
            getattr(self.o, attr).analyze.side_effect = lambda *a, **k: (
                time.sleep(0.2), {"tag": "ok"})[1]
        start = time.perf_counter()
        self.o.analyze(1, date(2026, 9, 5), self.db)
        elapsed = time.perf_counter() - start
        # 5并发 + 4并发 + 裁决 = 约 0.6s 量级；串行则 >= 2.0s
        self.assertLess(elapsed, 1.4, f"疑似未并行，耗时 {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
