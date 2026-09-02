"""今日管道过程接口测试"""
import unittest
from datetime import date, datetime

from app.routes.task_routes import get_pipeline_today


def make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def add_stage_log(db, stage, status, started, duration=None, error=None, summary=None):
    from app.models.task_log import TaskExecutionLog
    db.add(TaskExecutionLog(
        task_name=f"daily_pipeline.{stage}", status=status,
        started_at=started, finished_at=started if status != "running" else None,
        duration_seconds=duration, error_message=error, result_summary=summary,
    ))


class TestPipelineToday(unittest.TestCase):

    def test_no_run_not_started(self):
        db = make_db()
        resp = get_pipeline_today("daily_pipeline", db)
        d = resp.data
        self.assertEqual(d["pipeline_status"], "not_started")
        self.assertEqual(d["done_count"], 0)
        self.assertEqual(len(d["stages"]), 8)
        self.assertTrue(all(s["status"] == "not_started" for s in d["stages"]))

    def test_partial_progress_with_running_stage(self):
        from app.models.pipeline_checkpoint import PipelineCheckpoint
        db = make_db()
        db.add(PipelineCheckpoint(
            pipeline_name="daily_pipeline", run_date=date.today(),
            done_stages=["net_value", "quotes"], status="running",
        ))
        base = datetime(2026, 9, 2, 20, 0, 0)
        add_stage_log(db, "net_value", "success", base, 5.2, summary={"success_count": 10})
        add_stage_log(db, "quotes", "success", base, 12.0)
        add_stage_log(db, "rebalance", "running", base)
        db.commit()
        resp = get_pipeline_today("daily_pipeline", db)
        d = resp.data
        by_stage = {s["stage"]: s for s in d["stages"]}
        self.assertEqual(by_stage["net_value"]["status"], "done")
        self.assertEqual(by_stage["net_value"]["duration_seconds"], 5.2)
        self.assertEqual(by_stage["rebalance"]["status"], "running")
        self.assertEqual(by_stage["rotation_review"]["status"], "not_started")
        self.assertEqual(d["done_count"], 2)

    def test_failed_stage_reported(self):
        from app.models.pipeline_checkpoint import PipelineCheckpoint
        db = make_db()
        db.add(PipelineCheckpoint(
            pipeline_name="daily_pipeline", run_date=date.today(),
            done_stages=["net_value"], status="running",
            error_message="行情接口超时",
        ))
        add_stage_log(db, "net_value", "success", datetime(2026, 9, 2, 20, 0, 0), 3.0)
        add_stage_log(db, "quotes", "failed", datetime(2026, 9, 2, 20, 1, 0), 8.0,
                      error="行情接口超时")
        db.commit()
        resp = get_pipeline_today("daily_pipeline", db)
        by_stage = {s["stage"]: s for s in resp.data["stages"]}
        self.assertEqual(by_stage["quotes"]["status"], "failed")
        self.assertEqual(by_stage["quotes"]["error_message"], "行情接口超时")
        self.assertEqual(resp.data["pipeline_error"], "行情接口超时")


if __name__ == "__main__":
    unittest.main()
