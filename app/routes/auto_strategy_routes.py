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


# ========== 增强功能路由 ==========

@router.get("/enhanced/technical-indicators", response_model=APIResponse)
def get_technical_indicators(etf_code: str, db: Session = Depends(get_db)):
    """获取技术指标分析"""
    from app.services.technical_indicator_service import TechnicalIndicatorService
    
    svc = TechnicalIndicatorService()
    indicators = svc.calculate_all_indicators(etf_code, db)
    
    return APIResponse(data=indicators)


@router.get("/enhanced/market-sentiment-index", response_model=APIResponse)
def get_market_sentiment_index(target_date: date = None, db: Session = Depends(get_db)):
    """获取市场情绪指数"""
    from app.services.market_environment_service import MarketEnvironmentService
    
    if not target_date:
        target_date = date.today()
    
    svc = MarketEnvironmentService()
    index = svc.build_market_sentiment_index(target_date, db)
    
    return APIResponse(data=index)


@router.get("/enhanced/market-regime", response_model=APIResponse)
def get_market_regime(target_date: date = None, db: Session = Depends(get_db)):
    """识别市场阶段"""
    from app.services.market_environment_service import MarketEnvironmentService
    
    if not target_date:
        target_date = date.today()
    
    svc = MarketEnvironmentService()
    regime = svc.get_market_regime(target_date, db)
    
    return APIResponse(data=regime)


@router.get("/enhanced/similar-environments", response_model=APIResponse)
def find_similar_environments(
    strategy_id: int,
    target_date: date = None,
    top_k: int = 5,
    db: Session = Depends(get_db)
):
    """查找相似历史市场环境"""
    from app.services.market_environment_service import MarketEnvironmentService
    
    if not target_date:
        target_date = date.today()
    
    svc = MarketEnvironmentService()
    similar = svc.find_similar_market_environments(strategy_id, target_date, db, top_k)
    
    return APIResponse(data=similar)


@router.post("/enhanced/smart-experience-match", response_model=APIResponse)
def smart_match_experiences(strategy_id: int, db: Session = Depends(get_db)):
    """智能经验匹配"""
    from app.services.smart_experience_matcher import SmartExperienceMatcher
    
    matcher = SmartExperienceMatcher()
    current_scenario = matcher.get_current_market_scenario(date.today(), db)
    matched = matcher.match_experiences_by_scenario(strategy_id, current_scenario, db)
    
    return APIResponse(data={
        "current_scenario": current_scenario,
        "matched_experiences": matched,
        "total_matched": len(matched),
    })


@router.post("/enhanced/experience-conflict-detection", response_model=APIResponse)
def detect_experience_conflicts(strategy_id: int, db: Session = Depends(get_db)):
    """检测经验冲突"""
    from app.services.smart_experience_matcher import SmartExperienceMatcher
    
    matcher = SmartExperienceMatcher()
    experiences = db.query(Experience).filter(
        Experience.strategy_id == strategy_id,
        Experience.is_active == True,
    ).all()
    
    conflicts = matcher.detect_experience_conflicts(experiences)
    
    return APIResponse(data={
        "conflicts": conflicts,
        "total_conflicts": len(conflicts),
    })


@router.get("/enhanced/risk-dashboard", response_model=APIResponse)
def get_risk_dashboard(strategy_id: int, db: Session = Depends(get_db)):
    """获取风险仪表盘"""
    from app.services.risk_controller import RiskController
    
    controller = RiskController()
    dashboard = controller.get_risk_dashboard(strategy_id, db)
    
    return APIResponse(data=dashboard)


@router.get("/enhanced/circuit-breaker-check", response_model=APIResponse)
def check_circuit_breaker(strategy_id: int, db: Session = Depends(get_db)):
    """检查熔断条件"""
    from app.services.risk_controller import RiskController
    
    controller = RiskController()
    result = controller.check_circuit_breaker(strategy_id, db)
    
    return APIResponse(data=result)


@router.get("/enhanced/drawdown-protection", response_model=APIResponse)
def apply_drawdown_protection(strategy_id: int, db: Session = Depends(get_db)):
    """应用回撤保护"""
    from app.services.risk_controller import RiskController
    
    controller = RiskController()
    result = controller.apply_drawdown_protection(strategy_id, db)
    
    return APIResponse(data=result)


@router.get("/enhanced/stress-test", response_model=APIResponse)
def run_stress_test(strategy_id: int, db: Session = Depends(get_db)):
    """运行压力测试"""
    from app.services.risk_controller import RiskController
    
    controller = RiskController()
    result = controller.run_stress_test(strategy_id, db)
    
    return APIResponse(data=result)


@router.post("/enhanced/detect-anomalies", response_model=APIResponse)
def detect_anomalies(strategy_id: int, db: Session = Depends(get_db)):
    """检测异常情况"""
    from app.services.review_service import ReviewService
    
    svc = ReviewService()
    anomalies = svc.detect_anomalies(strategy_id, db)
    
    return APIResponse(data={
        "anomalies": anomalies,
        "total_anomalies": len(anomalies),
        "should_trigger_review": len(anomalies) > 0,
    })


@router.post("/enhanced/suggest-parameter-adjustments", response_model=APIResponse)
def suggest_parameter_adjustments(strategy_id: int, db: Session = Depends(get_db)):
    """建议参数调整"""
    from app.services.review_service import ReviewService
    
    svc = ReviewService()
    suggestions = svc.suggest_parameter_adjustments(strategy_id, db)
    
    return APIResponse(data=suggestions)


@router.post("/enhanced/full-risk-check", response_model=APIResponse)
def full_risk_check(strategy_id: int, db: Session = Depends(get_db)):
    """全面风险检查并可能暂停策略"""
    from app.services.risk_controller import RiskController
    
    controller = RiskController()
    
    circuit_breaker = controller.check_circuit_breaker(strategy_id, db)
    drawdown = controller.apply_drawdown_protection(strategy_id, db)
    budget = controller.check_risk_budget(strategy_id, db)
    
    should_pause = controller.should_pause_strategy(strategy_id, db)
    
    return APIResponse(data={
        "circuit_breaker": circuit_breaker,
        "drawdown": drawdown,
        "risk_budget": budget,
        "strategy_paused": should_pause,
        "overall_status": "paused" if should_pause else "running",
    })