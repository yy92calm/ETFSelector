"""管道检查点服务 - 记录每个阶段的完成状态，支持断点续跑"""

import logging
from datetime import date
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.pipeline_checkpoint import PipelineCheckpoint

logger = logging.getLogger(__name__)


class PipelineCheckpointService:
    """管道检查点服务 - 按 (pipeline, run_date) 记录已完成阶段，中断后可续跑"""

    def get_checkpoint(self, pipeline_name: str, run_date: date, db: Session) -> Optional[PipelineCheckpoint]:
        """获取指定管道日期的检查点"""
        return db.query(PipelineCheckpoint).filter(
            PipelineCheckpoint.pipeline_name == pipeline_name,
            PipelineCheckpoint.run_date == run_date,
        ).first()

    def get_done_stages(self, pipeline_name: str, run_date: date, db: Session) -> List[str]:
        """获取已完成阶段列表"""
        cp = self.get_checkpoint(pipeline_name, run_date, db)
        if not cp:
            return []
        return cp.done_stages or []

    def is_stage_done(self, pipeline_name: str, run_date: date, stage: str, db: Session) -> bool:
        """判断阶段是否已完成"""
        return stage in self.get_done_stages(pipeline_name, run_date, db)

    def mark_stage_done(self, pipeline_name: str, run_date: date, stage: str, db: Session):
        """标记阶段完成（幂等）"""
        cp = self.get_checkpoint(pipeline_name, run_date, db)
        if not cp:
            cp = PipelineCheckpoint(
                pipeline_name=pipeline_name,
                run_date=run_date,
                done_stages=[],
            )
            db.add(cp)
            db.flush()

        if stage not in (cp.done_stages or []):
            cp.done_stages = (cp.done_stages or []) + [stage]
            cp.status = "running"
            cp.error_message = None
        db.commit()
        logger.debug(f"[Checkpoint] {pipeline_name} {run_date} 完成阶段: {stage}")

    def mark_failed(self, pipeline_name: str, run_date: date, stage: str, error: str, db: Session):
        """标记阶段失败（不记入完成，供下次续跑重试）"""
        cp = self.get_checkpoint(pipeline_name, run_date, db)
        if not cp:
            cp = PipelineCheckpoint(
                pipeline_name=pipeline_name,
                run_date=run_date,
                done_stages=[],
            )
            db.add(cp)
            db.flush()
        cp.status = "failed"
        cp.error_message = f"{stage}: {error}"
        db.commit()
        logger.warning(f"[Checkpoint] {pipeline_name} {run_date} 阶段失败: {stage}: {error}")

    def mark_completed(self, pipeline_name: str, run_date: date, db: Session):
        """标记整个管道完成"""
        cp = self.get_checkpoint(pipeline_name, run_date, db)
        if not cp:
            cp = PipelineCheckpoint(
                pipeline_name=pipeline_name,
                run_date=run_date,
                done_stages=[],
            )
            db.add(cp)
        cp.status = "completed"
        cp.error_message = None
        db.commit()
        logger.info(f"[Checkpoint] {pipeline_name} {run_date} 全部完成")

    def reset(self, pipeline_name: str, run_date: date, db: Session):
        """重置检查点（用于重跑）"""
        cp = self.get_checkpoint(pipeline_name, run_date, db)
        if cp:
            db.delete(cp)
            db.commit()
            logger.info(f"[Checkpoint] {pipeline_name} {run_date} 检查点已重置")


_service: PipelineCheckpointService | None = None


def get_pipeline_checkpoint_service() -> PipelineCheckpointService:
    global _service
    if _service is None:
        _service = PipelineCheckpointService()
    return _service
