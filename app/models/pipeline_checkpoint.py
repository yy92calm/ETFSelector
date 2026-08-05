"""管道检查点模型 - 支持断点续跑"""

from sqlalchemy import Column, Integer, String, Date, DateTime, JSON
from datetime import datetime
from app.db.database import Base


class PipelineCheckpoint(Base):
    """管道执行检查点"""
    __tablename__ = "pipeline_checkpoint"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_name = Column(String(50), nullable=False, comment="管道名称: daily_pipeline/weekly_review")
    run_date = Column(Date, nullable=False, comment="执行日期")
    done_stages = Column(JSON, default=list, comment="已完成阶段列表")
    status = Column(String(20), default="running", comment="running/completed/failed")
    error_message = Column(String(500), nullable=True, comment="最近失败原因")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")

    def __repr__(self):
        return f"<PipelineCheckpoint {self.pipeline_name} {self.run_date}: {len(self.done_stages)}阶段完成>"
