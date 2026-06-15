"""
定时任务调度器
每个工作日执行串行管道：

  净值更新 → (间隔) 组合执行/舆情采集 → (间隔) AI分析+风险检查+策略调整

关键原则：每一步只在前一步完成后才执行，通过单个 job 内的串行调用实现。
"""

import logging
from datetime import date
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_scheduler: BackgroundScheduler | None = None


def _job_daily_pipeline():
    """
    每日自驱动串行管道

    阶段1 (18:00): 净值更新 + 组合再平衡
    阶段2 (20:00): 舆情采集 → AI分析 → 风险检查 → 策略调整
    """
    # ============================== 阶段1 ==============================
    _step_update_net_values()
    _step_run_strategies()

    # ============================== 阶段2 ==============================
    _step_collect_sentiments()
    _step_auto_pipeline()


def _step_update_net_values():
    """STEP 1: 更新ETF净值数据"""
    from app.db.database import SessionLocal
    from app.services.net_value_service import get_net_value_service

    logger.info("===== [阶段1] 更新ETF净值数据 =====")
    db = SessionLocal()
    try:
        svc = get_net_value_service()
        result = svc.batch_update_net_values(db)
        logger.info(f"净值更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}")
        if result.get('total', 0) > 6:
            logger.info(f"还有 {result['total'] - 6} 只ETF待更新，将在下一个周期继续")
    except Exception as e:
        logger.error(f"净值更新异常: {e}")
    finally:
        db.close()


def _step_run_strategies():
    """STEP 2: 所有活跃策略的再平衡检查（基于当前配置比例执行交易）"""
    from app.db.database import SessionLocal
    from app.services.portfolio_service import get_portfolio_service

    logger.info("===== [阶段1] 组合再平衡 =====")
    db = SessionLocal()
    try:
        svc = get_portfolio_service()
        svc.run_all_active_strategies(db)
        logger.info("组合再平衡完成")
    except Exception as e:
        logger.error(f"组合再平衡异常: {e}")
    finally:
        db.close()


def _step_collect_sentiments():
    """STEP 3: 舆情采集"""
    from app.db.database import SessionLocal
    from app.services.sentiment_service import SentimentService

    logger.info("===== [阶段2] 舆情采集 =====")
    db = SessionLocal()
    try:
        svc = SentimentService()
        result = svc.collect_daily_sentiment(date.today(), db)
        logger.info(f"舆情采集完成: {result.get('news_count', 0)}条")
    except Exception as e:
        logger.error(f"舆情采集异常: {e}")
    finally:
        db.close()


def _step_auto_pipeline():
    """
    STEP 4: AI 分析 + 风险检查 + 策略调整（串行管道）

    每只自动策略执行：
        风险检查（熔断/回撤）→ AI分析 → ETF验证 → 配置变更 → 交易执行
    """
    from app.db.database import SessionLocal
    from app.services.auto_strategy_executor import AutoStrategyExecutor

    logger.info("===== [阶段2] AI分析·风险检查·策略调整 =====")
    db = SessionLocal()
    try:
        svc = AutoStrategyExecutor()
        result = svc.run_all_auto_strategies(date.today(), db)
        logger.info(f"AI自驱动管道完成: {result}")
    except Exception as e:
        logger.error(f"AI自驱动管道异常: {e}")
    finally:
        db.close()


def _job_weekly_review():
    """每周复盘 - 每周日21:00"""
    from app.db.database import SessionLocal
    from app.services.review_service import ReviewService
    from app.models.strategy import Strategy

    logger.info("===== 每周复盘 =====")
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

        # ========== 工作日串行管道 ==========
        # 一个 job 内部顺序执行所有步骤，不存在并行竞态问题
        _scheduler.add_job(
            _job_daily_pipeline,
            trigger=CronTrigger(day_of_week='mon-fri', hour=settings.scheduler_hour, minute=settings.scheduler_minute),
            id="daily_auto_pipeline",
            replace_existing=True,
            misfire_grace_time=7200,
        )

        # ========== 每周复盘 ==========
        _scheduler.add_job(
            _job_weekly_review,
            trigger=CronTrigger(day_of_week='sun', hour=21, minute=0),
            id="weekly_review",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    return _scheduler
