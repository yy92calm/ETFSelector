"""定时任务执行日志模型"""

from sqlalchemy import Column, String, Integer, Float, DateTime, JSON
from datetime import datetime
from app.db.database import Base


class TaskExecutionLog(Base):
    """定时任务执行日志"""
    __tablename__ = "task_execution_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_name = Column(String(100), nullable=False, index=True, comment="任务名称")
    status = Column(String(20), nullable=False, comment="执行状态: running/success/failed")
    started_at = Column(DateTime, nullable=False, comment="开始时间")
    finished_at = Column(DateTime, nullable=True, comment="结束时间")
    duration_seconds = Column(Float, nullable=True, comment="执行耗时(秒)")
    result_summary = Column(JSON, nullable=True, comment="执行结果摘要")
    error_message = Column(String(1000), nullable=True, comment="错误信息")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")

    def __repr__(self):
        return f"<TaskExecutionLog {self.task_name} {self.status}>"

    def to_dict(self):
        return {
            "id": self.id,
            "task_name": self.task_name,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "result_summary": self.result_summary,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
