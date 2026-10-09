"""管道依赖阻断与异常口径：上游未成功则下游 skipped_dependency，阶段异常不再被吞成成功"""
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models.pipeline_checkpoint import PipelineCheckpoint
from app.models.task_log import TaskExecutionLog
from app.tasks import scheduler as sch


def make_factory():
    """同库多会话工厂（管道每阶段独立开关 Session）"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def stage_names():
    return [s[0] for s in sch.DAILY_PIPELINE_STAGES]


class PipelineRunner(unittest.TestCase):

    def setUp(self):
        self.factory = make_factory()

    def _seed_done(self, stages):
        from app.services.pipeline_checkpoint_service import get_pipeline_checkpoint_service
        db = self.factory()
        try:
            for stage in stages:
                get_pipeline_checkpoint_service().mark_stage_done(
                    "daily_pipeline", date.today(), stage, db)
        finally:
            db.close()

    def _run(self, raise_for=(), done=()):
        """用真实依赖表跑管道，阶段函数换成记录器（raise_for 内的阶段抛异常）"""
        self._seed_done(done)
        recorders = {
            name: (MagicMock(side_effect=RuntimeError(f"{name} 数据源超时"))
                   if name in raise_for else MagicMock())
            for name in stage_names()
        }
        plan = [(name, deps, recorders[name]) for name, deps, _fn in sch.DAILY_PIPELINE_STAGES]
        with patch.object(sch, "DAILY_PIPELINE_STAGES", plan), \
             patch("app.db.database.SessionLocal", self.factory), \
             patch.object(sch, "_record_failure_experience") as fail_exp:
            self._fail_exp = fail_exp
            sch._job_daily_pipeline.__wrapped__()
        return recorders

    def _logs(self):
        db = self.factory()
        try:
            return {l.task_name.split(".", 1)[1]: l
                    for l in db.query(TaskExecutionLog).filter(
                        TaskExecutionLog.task_name.like("daily_pipeline.%")).all()}
        finally:
            db.close()

    def _checkpoint(self):
        db = self.factory()
        try:
            return db.query(PipelineCheckpoint).first()
        finally:
            db.close()

    def test_all_success_runs_every_stage_and_completes(self):
        recorders = self._run()

        for name, rec in recorders.items():
            self.assertEqual(rec.call_count, 1, f"{name} 应执行一次")
        cp = self._checkpoint()
        self.assertEqual(cp.status, "completed")
        self.assertEqual(set(cp.done_stages), set(stage_names()))

    def test_quotes_failure_skips_all_downstream(self):
        recorders = self._run(raise_for=["quotes"])

        self.assertEqual(recorders["quotes"].call_count, 1)
        for name in ("rebalance", "market_scan", "market_regime",
                     "rotation_review", "autonomous"):
            self.assertEqual(recorders[name].call_count, 0, f"{name} 不应在行情失败时执行")
        # 与行情无关的采集阶段照常执行
        for name in ("net_value", "sentiment", "policy_flow", "sw_sector", "fundamental"):
            self.assertEqual(recorders[name].call_count, 1)

    def test_skipped_stage_is_logged_as_skipped_dependency(self):
        self._run(raise_for=["quotes"])

        log = self._logs()["market_scan"]
        self.assertEqual(log.status, "skipped")
        self.assertIn("skipped_dependency: quotes", log.error_message)
        self.assertEqual(self._logs()["quotes"].status, "failed")

    def test_checkpoint_not_marked_completed_and_unproductive_stages_not_done(self):
        self._run(raise_for=["quotes"])

        cp = self._checkpoint()
        self.assertNotEqual(cp.status, "completed")
        self.assertIn("quotes", cp.error_message)
        self.assertIn("market_scan", cp.error_message, "检查点应写明被依赖阻断的阶段")
        for stage in ("quotes", "market_scan", "market_regime",
                      "rotation_review", "autonomous"):
            self.assertNotIn(stage, cp.done_stages, "失败/跳过的阶段不能被标完成，否则续跑会跳过它")
        self.assertIn("net_value", cp.done_stages)

    def test_failure_experience_written_for_failing_stage_only(self):
        self._run(raise_for=["market_scan"])

        stages_logged = [c.args[1] for c in self._fail_exp.call_args_list]
        self.assertEqual(stages_logged, ["market_scan"], "被跳过的阶段不该再记一条失败经验")

    def test_dependency_block_propagates_transitively(self):
        """market_scan 失败 → market_regime 跳过 → 依赖它的阶段也跳过"""
        recorders = self._run(raise_for=["market_scan"])

        self.assertEqual(recorders["market_regime"].call_count, 0)
        self.assertEqual(recorders["rotation_review"].call_count, 0)
        self.assertEqual(recorders["autonomous"].call_count, 0)
        self.assertEqual(self._logs()["market_regime"].status, "skipped")

    def test_done_stages_from_checkpoint_are_not_blockers(self):
        """昨天/本轮已完成的阶段算成功，不阻断续跑的下游"""
        recorders = self._run(done=["quotes"])

        self.assertEqual(recorders["quotes"].call_count, 0, "已完成阶段应跳过执行")
        self.assertEqual(recorders["market_scan"].call_count, 1, "上游已完成不应阻断")
        self.assertEqual(self._checkpoint().status, "completed")

    def test_stage_table_covers_every_dispatched_stage(self):
        """阶段表漏项会让 _run_stage 拿不到函数或阶段永不执行"""
        self.assertEqual(len(stage_names()), len(set(stage_names())))
        for name, deps, fn in sch.DAILY_PIPELINE_STAGES:
            self.assertTrue(callable(fn), name)
            self.assertTrue(set(deps) <= set(stage_names()), f"{name} 依赖了未知阶段 {deps}")
            for dep in deps:
                self.assertLess(stage_names().index(dep), stage_names().index(name),
                                f"{name} 的依赖 {dep} 必须排在前面")


class AutonomousDualPath(unittest.TestCase):
    """阶段4：主路径失败必须降级，降级也失败必须让阶段失败并留痕"""

    def setUp(self):
        self.factory = make_factory()

    def _run(self, *, llm_key="sk-test", result=None, loop_error=None, fallback_error=None):
        loop_cls = MagicMock()
        if loop_error:
            loop_cls.return_value.run_autonomous.side_effect = loop_error
        else:
            loop_cls.return_value.run_autonomous.return_value = result
        fallback = MagicMock()
        if fallback_error:
            fallback.side_effect = fallback_error

        with patch("app.db.database.SessionLocal", self.factory), \
             patch("app.config.get_settings",
                   return_value=SimpleNamespace(llm_api_key=llm_key)), \
             patch("app.agent_core.loop.AgentLoop", loop_cls), \
             patch.object(sch, "_record_autonomous_analysis") as record, \
             patch.object(sch, "_step_auto_pipeline_fallback", fallback):
            try:
                sch._step_autonomous_decision()
                error = None
            except Exception as e:
                error = e
        return error, fallback, record

    def test_llm_error_falls_back(self):
        """run_autonomous 返回 error 时旧实现只跳过回写、阶段仍标成功，等于当日无决策"""
        error, fallback, record = self._run(
            result=SimpleNamespace(content="", tool_calls_made=[], error="LLM 调用失败"))

        self.assertIsNone(error)
        self.assertEqual(fallback.call_count, 1, "主路径失败应转降级管道")
        self.assertEqual(record.call_count, 0)

    def test_successful_decision_does_not_fall_back(self):
        error, fallback, record = self._run(
            result=SimpleNamespace(content="维持持仓", tool_calls_made=[], error=None))

        self.assertIsNone(error)
        self.assertEqual(fallback.call_count, 0)
        self.assertEqual(record.call_count, 1)

    def test_llm_not_configured_uses_fallback_only(self):
        error, fallback, record = self._run(llm_key="")

        self.assertIsNone(error)
        self.assertEqual(fallback.call_count, 1)
        self.assertEqual(record.call_count, 0)

    def test_both_paths_failed_marks_failed_both_paths(self):
        error, _fallback, _record = self._run(
            loop_error=RuntimeError("连接超时"), fallback_error=RuntimeError("executor 崩了"))

        self.assertIsNotNone(error)
        self.assertIn("failed_both_paths", str(error))
        self.assertIn("连接超时", str(error))
        self.assertIn("executor 崩了", str(error))

    def test_dual_failure_reaches_stage_log_and_experience(self):
        """阶段异常口径：抛出后由 _run_stage 记 failed + 写失败经验（含双路径标记）"""
        dual_error = "[failed_both_paths] 自主决策与降级管道均失败 | 主路径: 连接超时 | 降级: executor 崩了"
        with patch("app.db.database.SessionLocal", self.factory), \
             patch.object(sch, "_record_failure_experience") as fail_exp:
            from app.services.pipeline_checkpoint_service import get_pipeline_checkpoint_service
            cp_svc = get_pipeline_checkpoint_service()
            db = self.factory()
            try:
                for stage in ("quotes", "market_scan"):   # 上游已产出，autonomous 可执行
                    cp_svc.mark_stage_done("daily_pipeline", date.today(), stage, db)
            finally:
                db.close()

            plan = [(name, deps,
                     MagicMock(side_effect=RuntimeError(dual_error))
                     if name == "autonomous" else MagicMock())
                    for name, deps, _fn in sch.DAILY_PIPELINE_STAGES]
            with patch.object(sch, "DAILY_PIPELINE_STAGES", plan):
                sch._job_daily_pipeline.__wrapped__()

        self.assertEqual(self._log_status("autonomous"), "failed")
        self.assertIn("failed_both_paths", [c.args[2] for c in fail_exp.call_args_list][0])
        self.assertNotEqual(self._checkpoint_status(), "completed")

    def _log_status(self, stage):
        db = self.factory()
        try:
            log = db.query(TaskExecutionLog).filter_by(task_name=f"daily_pipeline.{stage}").first()
            return log.status if log else None
        finally:
            db.close()

    def _checkpoint_status(self):
        db = self.factory()
        try:
            cp = db.query(PipelineCheckpoint).first()
            return cp.status if cp else None
        finally:
            db.close()


class PolicyFlowStage(unittest.TestCase):
    def test_one_substep_failure_still_runs_the_other(self):
        with patch.object(sch, "_step_policy_impact", side_effect=RuntimeError("政策接口挂了")), \
             patch.object(sch, "_step_capital_flow") as flow:
            with self.assertRaises(RuntimeError) as ctx:
                sch._step_policy_flow()

        self.assertEqual(flow.call_count, 1, "政策失败不应连带跳过资金流向")
        self.assertIn("政策接口挂了", str(ctx.exception))

    def test_both_ok_no_raise(self):
        with patch.object(sch, "_step_policy_impact"), patch.object(sch, "_step_capital_flow"):
            sch._step_policy_flow()


if __name__ == "__main__":
    unittest.main()
