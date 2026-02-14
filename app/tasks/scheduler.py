"""
定时任务调度器
每日18:00获取全市场ETF行情，并执行所有活跃策略
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_scheduler: BackgroundScheduler | None = None


def _job_update_quotes():
    """定时任务：更新全市场行情"""
    from app.db.database import SessionLocal
    from app.services.data_service import get_data_service

    logger.info("===== 定时任务开始: 更新全市场ETF行情 =====")
    db = SessionLocal()
    try:
        svc = get_data_service()
        result = svc.update_today_quotes(db)
        logger.info(f"行情更新完成: {result}")
    except Exception as e:
        logger.error(f"行情更新异常: {e}")
    finally:
        db.close()


def _job_run_strategies():
    """定时任务：执行所有活跃策略（在行情更新之后运行）"""
    from app.db.database import SessionLocal
    from app.services.portfolio_service import get_portfolio_service

    logger.info("===== 定时任务开始: 执行策略 =====")
    db = SessionLocal()
    try:
        svc = get_portfolio_service()
        svc.run_all_active_strategies(db)
        logger.info("策略执行完成")
    except Exception as e:
        logger.error(f"策略执行异常: {e}")
    finally:
        db.close()


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()

        # 每日定时更新行情
        _scheduler.add_job(
            _job_update_quotes,
            trigger=CronTrigger(hour=settings.scheduler_hour, minute=settings.scheduler_minute),
            id="update_quotes",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # 行情更新后5分钟执行策略
        _scheduler.add_job(
            _job_run_strategies,
            trigger=CronTrigger(hour=settings.scheduler_hour, minute=settings.scheduler_minute + 5),
            id="run_strategies",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    return _scheduler
