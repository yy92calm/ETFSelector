"""运维工具 - 管道状态自查与数据/流程手动修复"""

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.tools.registry import tool

logger = logging.getLogger(__name__)

_DAILY_STAGES = ["net_value", "quotes", "rebalance", "sentiment", "policy_flow",
                 "market_scan", "market_regime", "fundamental", "rotation_review", "autonomous"]


@tool(name="get_pipeline_status", description="获取今日每日管道执行状态：各阶段完成情况、整体状态、最近失败原因")
def get_pipeline_status(db: Session) -> dict:
    from app.models.pipeline_checkpoint import PipelineCheckpoint

    today = date.today()
    cp = (
        db.query(PipelineCheckpoint)
        .filter(
            PipelineCheckpoint.pipeline_name == "daily_pipeline",
            PipelineCheckpoint.run_date == today,
        )
        .first()
    )
    if not cp:
        return {"run_date": today.isoformat(), "status": "not_started",
                "stages": {s: "pending" for s in _DAILY_STAGES}}
    done = cp.done_stages or []
    return {
        "run_date": cp.run_date.isoformat(),
        "status": cp.status,
        "error_message": cp.error_message,
        "stages": {s: ("done" if s in done else "pending") for s in _DAILY_STAGES},
    }


@tool(name="trigger_daily_pipeline", description="手动触发指定策略的每日自驱动管道（净值更新→舆情采集→AI全管道：风险检查→辩论分析→ETF验证→配置变更→交易执行），含多轮LLM调用，耗时数分钟")
def trigger_daily_pipeline(db: Session, strategy_id: int) -> dict:
    """触发每日管道

    Args:
    strategy_id: 策略ID
    """
    from app.models.strategy import Strategy
    from app.services.net_value_service import get_net_value_service
    from app.services.sentiment_service import SentimentService
    from app.services.auto_strategy_executor import AutoStrategyExecutor

    if not db.query(Strategy).filter(Strategy.id == strategy_id).first():
        return {"error": f"策略{strategy_id}不存在"}

    today = date.today()
    results = {}
    for key, fn in [
        ("net_value_update", lambda: get_net_value_service().batch_update_net_values(db, limit=6)),
        ("sentiment_collection", lambda: SentimentService().collect_daily_sentiment(today, db)),
        ("ai_pipeline", lambda: AutoStrategyExecutor().execute_full_pipeline(strategy_id, today, db)),
    ]:
        try:
            results[key] = {"status": "success", "detail": fn()}
        except Exception as e:
            logger.error(f"[ops] 管道步骤{key}失败: {e}", exc_info=True)
            results[key] = {"status": "failed", "error": str(e)}

    executor = AutoStrategyExecutor()
    pipeline = results.get("ai_pipeline", {})
    if pipeline.get("status") == "success" and isinstance(pipeline.get("detail"), dict):
        stages = pipeline["detail"].get("stages") or []
        results["ai_pipeline"]["failed_stages"] = [
            s.get("stage") for s in stages if isinstance(s, dict) and s.get("status") == "failed"
        ]
    return {"strategy_id": strategy_id, "execution_date": today.isoformat(), "steps": results}


@tool(name="trigger_sentiment_collect", description="手动触发当日舆情数据采集与情感分析（当日数据缺失或需补采时使用）")
def trigger_sentiment_collect(db: Session) -> dict:
    from app.services.sentiment_service import SentimentService

    return SentimentService().collect_daily_sentiment(date.today(), db)


@tool(name="catch_up_strategy", description="补跑指定策略：从创建日起逐日执行净值与再平衡到今天，用于新策略首次运行或数据断档修复")
def catch_up_strategy(db: Session, strategy_id: int) -> dict:
    """补跑策略

    Args:
    strategy_id: 策略ID
    """
    from app.models.strategy import Strategy
    from app.services.portfolio_service import get_portfolio_service

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略{strategy_id}不存在"}
    try:
        get_portfolio_service().catch_up_strategy(strategy, db)
        return {"strategy_id": strategy_id, "status": "success", "message": "策略补跑完成"}
    except Exception as e:
        logger.error(f"[ops] 策略{strategy_id}补跑失败: {e}", exc_info=True)
        return {"strategy_id": strategy_id, "status": "failed", "error": str(e)}


@tool(name="fetch_etf_history", description="从数据源补拉指定ETF历史行情并入库（回测或分析提示数据不足时的自助修复），ETF不在池中会自动加入")
def fetch_etf_history(db: Session, etf_code: str,
                      start_date: str = "20200101") -> dict:
    """补拉ETF历史行情

    Args:
    etf_code: 6位ETF代码，如 510300
    start_date: 起始日期 YYYYMMDD
    """
    from app.models.etf import ETFBasic
    from app.services.data_service import get_data_service

    svc = get_data_service()
    df = svc.fetch_etf_daily(etf_code, start_date=start_date)
    if df.empty:
        return {"error": f"未获取到 {etf_code} 的行情数据"}

    if not db.query(ETFBasic).filter(ETFBasic.etf_code == etf_code).first():
        db.add(ETFBasic(etf_code=etf_code, etf_name=etf_code))
        db.commit()

    added = svc.save_daily_quotes(etf_code, df, db)
    return {"etf_code": etf_code, "new_records": added, "total_rows": len(df)}
