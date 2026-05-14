"""
定时任务调度器
每个工作日执行：净值更新、策略执行、舆情采集、AI分析、自动策略调整
"""

import logging
from datetime import date
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
        result = svc.batch_update_net_values(db, limit=6)
        logger.info(f"净值更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}")
        
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


def _job_collect_sentiments():
    """19:30 - 舆情采集任务"""
    from app.db.database import SessionLocal
    from app.services.sentiment_service import SentimentService

    logger.info("===== 定时任务开始: 舆情采集 =====")
    db = SessionLocal()
    try:
        svc = SentimentService()
        result = svc.collect_daily_sentiment(date.today(), db)
        logger.info(f"舆情采集完成: {result['news_count']}条")
    except Exception as e:
        logger.error(f"舆情采集异常: {e}")
    finally:
        db.close()


def _job_analyze_market():
    """19:45 - AI市场分析任务"""
    from app.db.database import SessionLocal
    from app.services.auto_analysis_service import AutoAnalysisService
    from app.models.strategy import Strategy

    logger.info("===== 定时任务开始: AI市场分析 =====")
    db = SessionLocal()
    try:
        svc = AutoAnalysisService()
        auto_strategies = db.query(Strategy).filter(
            Strategy.strategy_source == 'auto_generated',
            Strategy.auto_strategy_status == 'running'
        ).all()
        
        for strategy in auto_strategies:
            result = svc.analyze_market(strategy.id, date.today(), db)
            logger.info(f"策略{strategy.id}分析: {result.get('market_sentiment', 'N/A')}")
    except Exception as e:
        logger.error(f"AI分析异常: {e}")
    finally:
        db.close()


def _job_adjust_auto_strategy():
    """20:00 - 自动策略调整任务"""
    from app.db.database import SessionLocal
    from app.services.auto_strategy_executor import AutoStrategyExecutor

    logger.info("===== 定时任务开始: 自动策略调整 =====")
    db = SessionLocal()
    try:
        svc = AutoStrategyExecutor()
        result = svc.run_all_auto_strategies(date.today(), db)
        logger.info(f"自动策略执行: {result}")
    except Exception as e:
        logger.error(f"自动策略调整异常: {e}")
    finally:
        db.close()


def _job_weekly_review():
    """每周复盘 - 每周日21:00"""
    from app.db.database import SessionLocal
    from app.services.review_service import ReviewService
    from app.models.strategy import Strategy

    logger.info("===== 定时任务开始: 每周复盘 =====")
    db = SessionLocal()
    try:
        svc = ReviewService()
        auto_strategies = db.query(Strategy).filter(
            Strategy.strategy_source == 'auto_generated',
            Strategy.auto_strategy_status == 'running'
        ).all()
        
        for strategy in auto_strategies:
            result = svc.trigger_review(strategy.id, 'weekly', db)
            logger.info(f"策略{strategy.id}每周复盘: {result}")
    except Exception as e:
        logger.error(f"每周复盘异常: {e}")
    finally:
        db.close()


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()

        _scheduler.add_job(
            _job_update_net_values,
            trigger=CronTrigger(day_of_week='mon-fri', hour=settings.scheduler_hour, minute=settings.scheduler_minute),
            id="update_net_values",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        _scheduler.add_job(
            _job_run_strategies,
            trigger=CronTrigger(day_of_week='mon-fri', hour=settings.scheduler_hour, minute=settings.scheduler_minute + 5),
            id="run_strategies",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        _scheduler.add_job(
            _job_collect_sentiments,
            trigger=CronTrigger(day_of_week='mon-fri', hour=19, minute=30),
            id="collect_sentiments",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        _scheduler.add_job(
            _job_analyze_market,
            trigger=CronTrigger(day_of_week='mon-fri', hour=19, minute=45),
            id="analyze_market",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        _scheduler.add_job(
            _job_adjust_auto_strategy,
            trigger=CronTrigger(day_of_week='mon-fri', hour=20, minute=0),
            id="adjust_auto_strategy",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        _scheduler.add_job(
            _job_weekly_review,
            trigger=CronTrigger(day_of_week='sun', hour=21, minute=0),
            id="weekly_review",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    return _scheduler
