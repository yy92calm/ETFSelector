"""定时任务 API — 历史记录 + 手动触发 + 检查点管理"""

import logging
from datetime import datetime, timedelta, date
from typing import Optional
from fastapi import APIRouter, Depends, Query, BackgroundTasks
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


# ------------------------------------------------------------------ #
#  手动触发
# ------------------------------------------------------------------ #

# 允许手动触发的阶段及其对应函数
_STAGE_FUNCS = {
    "net_value": "app.tasks.scheduler:_step_update_net_values",
    "quotes": "app.tasks.scheduler:_step_update_quotes",
    "rebalance": "app.tasks.scheduler:_step_run_strategies",
    "sentiment": "app.tasks.scheduler:_step_collect_sentiments",
    "policy_flow": "app.tasks.scheduler:_step_policy_impact",
    "capital_flow": "app.tasks.scheduler:_step_capital_flow",
    "market_scan": "app.tasks.scheduler:_step_market_scan",
    "rotation_review": "app.tasks.scheduler:_step_rotation_review",
    "autonomous": "app.tasks.scheduler:_step_autonomous_decision",
}


def _import_func(dotted_path: str):
    """从 'module.path:func_name' 导入函数"""
    import importlib
    module_path, func_name = dotted_path.rsplit(":", 1)
    mod = importlib.import_module(module_path)
    return getattr(mod, func_name)


@router.post("/trigger/daily-pipeline", response_model=APIResponse)
def trigger_daily_pipeline(
    background_tasks: BackgroundTasks,
    reset_checkpoint: bool = Query(False, description="是否重置检查点（从头重跑）"),
    db: Session = Depends(get_db),
):
    """
    手动触发每日完整管道（8个阶段串行执行）。
    默认从上次断点续跑；reset_checkpoint=True 时清空检查点从头开始。
    """
    if reset_checkpoint:
        from app.services.pipeline_checkpoint_service import get_pipeline_checkpoint_service
        cp_svc = get_pipeline_checkpoint_service()
        cp_svc.reset("daily_pipeline", date.today(), db)
        logger.info("[手动触发] 检查点已重置，将从头执行管道")

    from app.tasks.scheduler import _job_daily_pipeline
    background_tasks.add_task(_job_daily_pipeline)
    return APIResponse(message="每日管道已提交后台执行")


@router.post("/trigger/weekly-review", response_model=APIResponse)
def trigger_weekly_review(background_tasks: BackgroundTasks):
    """手动触发每周复盘"""
    from app.tasks.scheduler import _job_weekly_review
    background_tasks.add_task(_job_weekly_review)
    return APIResponse(message="每周复盘已提交后台执行")


@router.post("/trigger/auto-fetch-quotes", response_model=APIResponse)
def trigger_auto_fetch_quotes(background_tasks: BackgroundTasks):
    """手动触发LLM自动行情补全"""
    from app.tasks.scheduler import _job_auto_fetch_quotes
    background_tasks.add_task(_job_auto_fetch_quotes)
    return APIResponse(message="行情补全已提交后台执行")


@router.post("/trigger/stage/{stage_name}", response_model=APIResponse)
def trigger_single_stage(
    stage_name: str,
    background_tasks: BackgroundTasks,
):
    """
    手动触发单个管道阶段。

    可选阶段: net_value / quotes / rebalance / sentiment / policy_flow /
              capital_flow / market_scan / rotation_review / autonomous
    """
    if stage_name not in _STAGE_FUNCS:
        return APIResponse(code=400, message=f"未知阶段: {stage_name}，可选: {list(_STAGE_FUNCS.keys())}")

    fn = _import_func(_STAGE_FUNCS[stage_name])
    background_tasks.add_task(fn)
    return APIResponse(message=f"阶段 [{stage_name}] 已提交后台执行")


# ------------------------------------------------------------------ #
#  检查点管理
# ------------------------------------------------------------------ #

@router.get("/checkpoint", response_model=APIResponse)
def get_checkpoint(
    pipeline: str = Query("daily_pipeline"),
    run_date: Optional[str] = Query(None, description="日期 YYYY-MM-DD，默认今天"),
    db: Session = Depends(get_db),
):
    """查看管道检查点状态"""
    from app.models.pipeline_checkpoint import PipelineCheckpoint

    target_date = date.fromisoformat(run_date) if run_date else date.today()
    cp = (
        db.query(PipelineCheckpoint)
        .filter(
            PipelineCheckpoint.pipeline_name == pipeline,
            PipelineCheckpoint.run_date == target_date,
        )
        .first()
    )

    if not cp:
        return APIResponse(data={
            "pipeline": pipeline,
            "run_date": target_date.isoformat(),
            "status": "not_started",
            "done_stages": [],
            "error_message": None,
        })

    return APIResponse(data={
        "pipeline": cp.pipeline_name,
        "run_date": cp.run_date.isoformat(),
        "status": cp.status,
        "done_stages": cp.done_stages or [],
        "error_message": cp.error_message,
        "updated_at": cp.updated_at.isoformat() if cp.updated_at else None,
    })


@router.post("/checkpoint/reset", response_model=APIResponse)
def reset_checkpoint(
    pipeline: str = Query("daily_pipeline"),
    run_date: Optional[str] = Query(None, description="日期 YYYY-MM-DD，默认今天"),
    db: Session = Depends(get_db),
):
    """重置管道检查点（用于重跑失败的管道）"""
    from app.services.pipeline_checkpoint_service import get_pipeline_checkpoint_service

    target_date = date.fromisoformat(run_date) if run_date else date.today()
    cp_svc = get_pipeline_checkpoint_service()
    cp_svc.reset(pipeline, target_date, db)
    return APIResponse(message=f"检查点已重置: {pipeline} {target_date}")
