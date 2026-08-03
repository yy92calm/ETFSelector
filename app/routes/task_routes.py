"""定时任务 API"""

import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db.database import get_db
from app.schemas.schemas import APIResponse
from app.models.task_log import TaskExecutionLog

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["定时任务"])


@router.get("/history", response_model=APIResponse)
def get_task_history(
    days: int = Query(7, ge=1, le=30, description="查询最近N天"),
    limit: int = Query(50, ge=1, le=200, description="返回条数上限"),
    db: Session = Depends(get_db)
):
    """获取定时任务执行历史"""
    cutoff = datetime.utcnow() - timedelta(days=days)

    logs = (
        db.query(TaskExecutionLog)
        .filter(TaskExecutionLog.started_at >= cutoff)
        .order_by(desc(TaskExecutionLog.started_at))
        .limit(limit)
        .all()
    )

    return APIResponse(data={
        "logs": [log.to_dict() for log in logs],
        "total": len(logs),
        "days": days,
    })


@router.get("/stats", response_model=APIResponse)
def get_task_stats(days: int = Query(7, ge=1, le=30), db: Session = Depends(get_db)):
    """获取任务执行统计"""
    cutoff = datetime.utcnow() - timedelta(days=days)

    logs = (
        db.query(TaskExecutionLog)
        .filter(TaskExecutionLog.started_at >= cutoff)
        .all()
    )

    # 按任务名分组统计
    stats = {}
    for log in logs:
        name = log.task_name
        if name not in stats:
            stats[name] = {"total": 0, "success": 0, "failed": 0, "running": 0, "avg_duration": 0}
        stats[name]["total"] += 1
        if log.status in stats[name]:
            stats[name][log.status] += 1

    # 计算平均耗时
    for name in stats:
        durations = [
            log.duration_seconds for log in logs
            if log.task_name == name and log.duration_seconds is not None
        ]
        if durations:
            stats[name]["avg_duration"] = round(sum(durations) / len(durations), 2)

    return APIResponse(data={
        "stats": stats,
        "days": days,
        "total_executions": len(logs),
    })
