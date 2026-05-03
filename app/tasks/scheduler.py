"""
定时任务调度器
每个工作日20:00更新ETF净值数据（基于证监会官方数据）
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_scheduler: BackgroundScheduler | None = None


def _job_update_net_values():
    """定时任务：更新ETF净值数据（从证监会获取）"""
    from app.db.database import SessionLocal
    from app.services.net_value_service import get_net_value_service

    logger.info("===== 定时任务开始: 更新ETF净值数据 =====")
    db = SessionLocal()
    try:
        svc = get_net_value_service()
        # 每次更新6只ETF（遵守证监会频率限制：1分钟6次）
        result = svc.batch_update_net_values(db, limit=6)
        logger.info(f"净值更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}")
        
        # 如果还有更多ETF未更新，继续更新下一批
        if result['total_etfs'] > 6:
            logger.info(f"还有 {result['total_etfs'] - 6} 只ETF待更新，将在下一个定时周期继续")
    except Exception as e:
        logger.error(f"净值更新异常: {e}")
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

        # 每个工作日20:00更新净值数据（周一到周五）
        _scheduler.add_job(
            _job_update_net_values,
            trigger=CronTrigger(
                day_of_week='mon-fri',  # 工作日（周一到周五）
                hour=settings.scheduler_hour,
                minute=settings.scheduler_minute
            ),
            id="update_net_values",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # 净值更新后5分钟执行策略
        _scheduler.add_job(
            _job_run_strategies,
            trigger=CronTrigger(
                day_of_week='mon-fri',
                hour=settings.scheduler_hour,
                minute=settings.scheduler_minute + 5
            ),
            id="run_strategies",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    return _scheduler
