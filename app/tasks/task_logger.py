"""定时任务执行日志工具"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any
from functools import wraps

logger = logging.getLogger(__name__)


def log_task_execution(task_name: str):
    """任务执行日志装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            from app.db.database import SessionLocal
            from app.models.task_log import TaskExecutionLog

            db = SessionLocal()
            try:
                # 记录开始
                log_entry = TaskExecutionLog(
                    task_name=task_name,
                    status="running",
                    started_at=datetime.utcnow()
                )
                db.add(log_entry)
                db.commit()
                log_id = log_entry.id

                # 执行任务
                try:
                    result = func(*args, **kwargs)

                    # 记录成功
                    log_entry.status = "success"
                    log_entry.finished_at = datetime.utcnow()
                    log_entry.duration_seconds = (log_entry.finished_at - log_entry.started_at).total_seconds()
                    log_entry.result_summary = _summarize_result(result)
                    db.commit()

                    logger.info(f"任务 {task_name} 执行成功，耗时 {log_entry.duration_seconds:.2f}s")
                    return result

                except Exception as e:
                    # 记录失败
                    log_entry.status = "failed"
                    log_entry.finished_at = datetime.utcnow()
                    log_entry.duration_seconds = (log_entry.finished_at - log_entry.started_at).total_seconds()
                    log_entry.error_message = str(e)[:1000]
                    db.commit()

                    logger.error(f"任务 {task_name} 执行失败: {e}")
                    raise

            finally:
                db.close()

        return wrapper
    return decorator


def _summarize_result(result: Any) -> Optional[Dict]:
    """提取结果摘要"""
    if result is None:
        return None
    if isinstance(result, dict):
        # 只保留关键字段，避免过大
        summary = {}
        for key in ["success_count", "fail_count", "total", "status", "message", "count"]:
            if key in result:
                summary[key] = result[key]
        return summary if summary else {"type": "dict", "keys": list(result.keys())[:10]}
    if isinstance(result, (list, tuple)):
        return {"type": "list", "length": len(result)}
    return {"type": type(result).__name__, "value": str(result)[:200]}
