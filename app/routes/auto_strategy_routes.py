"""AI全自动策略路由"""

import logging
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse
from app.models.strategy import Strategy
from app.models.auto_strategy_log import AutoStrategyLog
from app.models.sentiment import SentimentData
from app.models.experience import Experience
from app.models.etf import ETFBasic

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auto-strategy", tags=["AI全自动策略"])


@router.get("/status", response_model=APIResponse)
def get_auto_strategy_status(strategy_id: int, db: Session = Depends(get_db)):
    """获取自动策略状态"""
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    
    return APIResponse(data={
        "strategy_id": strategy_id,
        "name": strategy.name,
        "source": strategy.strategy_source,
        "status": strategy.auto_strategy_status,
        "last_analysis": strategy.last_auto_analysis_date.isoformat() if strategy.last_auto_analysis_date else None,
        "adjustment_count": strategy.auto_adjustment_count,
        "current_allocation": strategy.allocation_config,
        "last_analysis_result": strategy.last_analysis_result,
        "enable_memory": strategy.enable_memory,
    })


@router.get("/logs", response_model=APIResponse)
def get_execution_logs(strategy_id: int, days: int = 7, db: Session = Depends(get_db)):
    """获取执行日志"""
    logs = db.query(AutoStrategyLog).filter(
        AutoStrategyLog.strategy_id == strategy_id,
        AutoStrategyLog.log_date >= date.today() - timedelta(days=days)
    ).order_by(AutoStrategyLog.log_date.desc()).limit(50).all()
    
    return APIResponse(data={
        "logs": [log.to_dict() for log in logs],
        "total": len(logs),
    })


@router.get("/sentiments", response_model=APIResponse)
def get_sentiment_data(days: int = 1, db: Session = Depends(get_db)):
    """获取舆情数据"""
    sentiments = db.query(SentimentData).filter(
        SentimentData.data_date >= date.today() - timedelta(days=days)
    ).order_by(SentimentData.publish_time.desc()).limit(100).all()
    
    return APIResponse(data={
        "sentiments": [s.to_dict() for s in sentiments],
        "total": len(sentiments),
        "date": date.today().isoformat(),
    })


@router.get("/sentiments/summary", response_model=APIResponse)
def get_sentiment_summary(target_date: date = None, db: Session = Depends(get_db)):
    """获取舆情汇总"""
    from app.services.sentiment_service import SentimentService
    
    if not target_date:
        target_date = date.today()
    
    svc = SentimentService()
    summary = svc.get_sentiment_summary(target_date, db)
    
    return APIResponse(data=summary)


@router.get("/experiences", response_model=APIResponse)
def get_experiences(strategy_id: int, db: Session = Depends(get_db)):
    """获取策略经验"""
    experiences = db.query(Experience).filter(
        Experience.strategy_id == strategy_id,
        Experience.is_active == True,
    ).order_by(Experience.effectiveness_score.desc()).limit(20).all()
    
    return APIResponse(data={
        "experiences": [exp.to_dict() for exp in experiences],
        "total": len(experiences),
    })


@router.post("/trigger-collect", response_model=APIResponse)
def trigger_sentiment_collect(db: Session = Depends(get_db)):
    """手动触发舆情采集"""
    from app.services.sentiment_service import SentimentService
    
    svc = SentimentService()
    result = svc.collect_daily_sentiment(date.today(), db)
    
    return APIResponse(message="舆情采集完成", data=result)


@router.post("/trigger-analyze", response_model=APIResponse)
def trigger_market_analyze(strategy_id: int, db: Session = Depends(get_db)):
    """手动触发市场分析"""
    from app.services.auto_analysis_service import AutoAnalysisService
    
    svc = AutoAnalysisService()
    result = svc.analyze_market(strategy_id, date.today(), db)
    
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return APIResponse(message="市场分析完成", data=result)


@router.post("/trigger-adjust", response_model=APIResponse)
def trigger_strategy_adjust(strategy_id: int, db: Session = Depends(get_db)):
    """手动触发策略调整"""
    from app.services.auto_strategy_executor import AutoStrategyExecutor
    
    svc = AutoStrategyExecutor()
    result = svc.execute_auto_strategy(strategy_id, date.today(), db)
    
    return APIResponse(message="策略调整完成", data=result)


@router.post("/trigger-review", response_model=APIResponse)
def trigger_review(strategy_id: int, review_type: str = "weekly", db: Session = Depends(get_db)):
    """手动触发复盘"""
    from app.services.review_service import ReviewService
    
    svc = ReviewService()
    result = svc.trigger_review(strategy_id, review_type, db)
    
    return APIResponse(message="复盘完成", data=result)


@router.post("/trigger-daily-pipeline", response_model=APIResponse)
def trigger_daily_pipeline(strategy_id: int, db: Session = Depends(get_db)):
    """一键触发每日自驱动管道"""
    from app.services.net_value_service import get_net_value_service
    from app.services.portfolio_service import get_portfolio_service
    from app.services.sentiment_service import SentimentService
    from app.services.auto_analysis_service import AutoAnalysisService
    from app.services.auto_strategy_executor import AutoStrategyExecutor
    
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
        
    results = {}
    today = date.today()
    
    # 步骤1: 更新净值行情 (行情模块)
    try:
        nv_svc = get_net_value_service()
        nv_result = nv_svc.batch_update_net_values(db, limit=6)
        results["net_value_update"] = {"status": "success", "detail": nv_result}
    except Exception as e:
        logger.error(f"每日管道-净值更新失败: {e}", exc_info=True)
        results["net_value_update"] = {"status": "failed", "error": str(e)}
        
    # 步骤2: 组合再平衡交易 (交易执行)
    try:
        port_svc = get_portfolio_service()
        port_svc.run_strategy_for_date(strategy, today, db)
        results["portfolio_rebalance"] = {"status": "success"}
    except Exception as e:
        logger.error(f"每日管道-再平衡失败: {e}", exc_info=True)
        results["portfolio_rebalance"] = {"status": "failed", "error": str(e)}
        
    # 步骤3: 采集舆情与观点情绪 (舆情模块)
    try:
        sent_svc = SentimentService()
        sent_result = sent_svc.collect_daily_sentiment(today, db)
        results["sentiment_collection"] = {"status": "success", "detail": sent_result}
    except Exception as e:
        logger.error(f"每日管道-舆情采集失败: {e}", exc_info=True)
        results["sentiment_collection"] = {"status": "failed", "error": str(e)}
        
    # 步骤4: AI舆情与市场环境分析 (AI市场分析)
    try:
        analysis_svc = AutoAnalysisService()
        analysis_result = analysis_svc.analyze_market(strategy_id, today, db)
        results["ai_market_analysis"] = {"status": "success", "detail": analysis_result}
    except Exception as e:
        logger.error(f"每日管道-AI市场分析失败: {e}", exc_info=True)
        results["ai_market_analysis"] = {"status": "failed", "error": str(e)}
        
    # 步骤5: 策略AI驱动权重分配调整 (策略调整)
    try:
        executor_svc = AutoStrategyExecutor()
        adjust_result = executor_svc.execute_auto_strategy(strategy_id, today, db)
        results["strategy_adjustment"] = {"status": "success", "detail": adjust_result}
    except Exception as e:
        logger.error(f"每日管道-策略调整失败: {e}", exc_info=True)
        results["strategy_adjustment"] = {"status": "failed", "error": str(e)}
        
    return APIResponse(message="一键每日管道运行完成", data=results)


@router.get("/review-report", response_model=APIResponse)
def get_review_report(strategy_id: int, review_type: str = "weekly", db: Session = Depends(get_db)):
    """获取复盘报告（不触发LLM）"""
    from app.services.review_service import ReviewService
    
    svc = ReviewService()
    report = svc.get_review_report(strategy_id, review_type, db)
    
    return APIResponse(data=report)


@router.post("/create", response_model=APIResponse)
def create_auto_strategy(req: dict, db: Session = Depends(get_db)):
    """创建自动策略"""
    initial_allocation = req.get("initial_allocation")
    
    if not initial_allocation:
        etfs = db.query(ETFBasic).limit(4).all()
        if etfs:
            ratio = 1.0 / len(etfs)
            initial_allocation = {etf.etf_code: round(ratio, 2) for etf in etfs}
            total = sum(initial_allocation.values())
            if total < 1.0:
                first_code = list(initial_allocation.keys())[0]
                initial_allocation[first_code] += round(1.0 - total, 2)
        else:
            initial_allocation = {}
    
    strategy = Strategy(
        name=req.get("name", "自动策略"),
        description=req.get("description", "AI全自动驱动策略"),
        strategy_source='auto_generated',
        auto_strategy_status='running',
        allocation_config=initial_allocation,
        max_daily_adjustments=req.get("max_daily_adjustments", 1),
        initial_capital=req.get("initial_capital", 100000),
        enable_memory=req.get("enable_memory", True),
        strategy_type='auto',
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    
    logger.info(f"创建自动策略: {strategy.id}")
    
    return APIResponse(message="自动策略创建成功", data={"strategy_id": strategy.id})


@router.post("/pause", response_model=APIResponse)
def pause_auto_strategy(strategy_id: int, db: Session = Depends(get_db)):
    """暂停自动策略"""
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    
    strategy.auto_strategy_status = 'paused'
    db.commit()
    
    return APIResponse(message="策略已暂停")


@router.post("/resume", response_model=APIResponse)
def resume_auto_strategy(strategy_id: int, db: Session = Depends(get_db)):
    """恢复自动策略"""
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    
    strategy.auto_strategy_status = 'running'
    db.commit()
    
    return APIResponse(message="策略已恢复")


@router.get("/list", response_model=APIResponse)
def list_auto_strategies(db: Session = Depends(get_db)):
    """获取所有自动策略"""
    strategies = db.query(Strategy).filter(
        Strategy.strategy_source == 'auto_generated'
    ).all()
    
    return APIResponse(data={
        "strategies": [{
            "id": s.id,
            "name": s.name,
            "status": s.auto_strategy_status,
            "allocation": s.allocation_config,
            "adjustment_count": s.auto_adjustment_count,
            "last_analysis": s.last_auto_analysis_date.isoformat() if s.last_auto_analysis_date else None,
        } for s in strategies],
        "total": len(strategies),
    })