"""增强版自动策略路由 - 集成所有优化功能"""

import logging
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/enhanced-auto-strategy", tags=["增强版AI策略"])


@router.get("/technical-indicators", response_model=APIResponse)
def get_technical_indicators(etf_code: str, db: Session = Depends(get_db)):
    """获取技术指标分析"""
    from app.services.technical_indicator_service import TechnicalIndicatorService
    
    svc = TechnicalIndicatorService()
    indicators = svc.calculate_all_indicators(etf_code, db)
    
    return APIResponse(data=indicators)


@router.get("/batch-technical-indicators", response_model=APIResponse)
def get_batch_technical_indicators(etf_codes: str, db: Session = Depends(get_db)):
    """批量获取技术指标"""
    from app.services.technical_indicator_service import TechnicalIndicatorService
    
    codes_list = etf_codes.split(",")
    svc = TechnicalIndicatorService()
    indicators = svc.batch_calculate_indicators(codes_list, db)
    
    return APIResponse(data=indicators)


@router.get("/market-sentiment-index", response_model=APIResponse)
def get_market_sentiment_index(target_date: date = None, db: Session = Depends(get_db)):
    """获取市场情绪指数"""
    from app.services.market_environment_service import MarketEnvironmentService
    
    if not target_date:
        target_date = date.today()
    
    svc = MarketEnvironmentService()
    index = svc.build_market_sentiment_index(target_date, db)
    
    return APIResponse(data=index)


@router.get("/market-regime", response_model=APIResponse)
def get_market_regime(target_date: date = None, db: Session = Depends(get_db)):
    """识别市场阶段"""
    from app.services.market_environment_service import MarketEnvironmentService
    
    if not target_date:
        target_date = date.today()
    
    svc = MarketEnvironmentService()
    regime = svc.get_market_regime(target_date, db)
    
    return APIResponse(data=regime)


@router.get("/market-volatility", response_model=APIResponse)
def get_market_volatility(days: int = 20, db: Session = Depends(get_db)):
    """计算市场波动率"""
    from app.services.market_environment_service import MarketEnvironmentService
    
    svc = MarketEnvironmentService()
    volatility = svc.calculate_market_volatility(db, days)
    
    return APIResponse(data=volatility)


@router.get("/similar-environments", response_model=APIResponse)
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


@router.post("/smart-experience-match", response_model=APIResponse)
def smart_match_experiences(strategy_id: int, db: Session = Depends(get_db)):
    """智能经验匹配"""
    from app.services.smart_experience_matcher import SmartExperienceMatcher
    from app.services.market_environment_service import MarketEnvironmentService
    
    matcher = SmartExperienceMatcher()
    env_svc = MarketEnvironmentService()
    
    current_scenario = matcher.get_current_market_scenario(date.today(), db)
    matched = matcher.match_experiences_by_scenario(strategy_id, current_scenario, db)
    
    return APIResponse(data={
        "current_scenario": current_scenario,
        "matched_experiences": matched,
        "total_matched": len(matched),
    })


@router.post("/experience-conflict-detection", response_model=APIResponse)
def detect_experience_conflicts(strategy_id: int, db: Session = Depends(get_db)):
    """检测经验冲突"""
    from app.services.smart_experience_matcher import SmartExperienceMatcher
    from app.models.experience import Experience
    
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


@router.post("/update-experience-weights", response_model=APIResponse)
def update_experience_weights(strategy_id: int, db: Session = Depends(get_db)):
    """更新经验权重"""
    from app.services.smart_experience_matcher import SmartExperienceMatcher
    
    matcher = SmartExperienceMatcher()
    updated_count = matcher.update_experience_weights(strategy_id, db)
    
    return APIResponse(message=f"更新{updated_count}条经验权重", data={"updated_count": updated_count})


@router.post("/boost-failure-experiences", response_model=APIResponse)
def boost_failure_experiences(strategy_id: int, db: Session = Depends(get_db)):
    """强化失败经验权重"""
    from app.services.experience_manager import ExperienceManager
    
    manager = ExperienceManager()
    boosted_count = manager.boost_failure_experience_weights(strategy_id, db)
    
    return APIResponse(message=f"强化{boosted_count}条失败经验权重", data={"boosted_count": boosted_count})


@router.get("/risk-dashboard", response_model=APIResponse)
def get_risk_dashboard(strategy_id: int, db: Session = Depends(get_db)):
    """获取风险仪表盘"""
    from app.services.risk_controller import RiskController
    
    controller = RiskController()
    dashboard = controller.get_risk_dashboard(strategy_id, db)
    
    return APIResponse(data=dashboard)


@router.get("/circuit-breaker-check", response_model=APIResponse)
def check_circuit_breaker(strategy_id: int, db: Session = Depends(get_db)):
    """检查熔断条件"""
    from app.services.risk_controller import RiskController
    
    controller = RiskController()
    result = controller.check_circuit_breaker(strategy_id, db)
    
    return APIResponse(data=result)


@router.get("/drawdown-protection", response_model=APIResponse)
def apply_drawdown_protection(strategy_id: int, db: Session = Depends(get_db)):
    """应用回撤保护"""
    from app.services.risk_controller import RiskController
    
    controller = RiskController()
    result = controller.apply_drawdown_protection(strategy_id, db)
    
    return APIResponse(data=result)


@router.get("/risk-budget-check", response_model=APIResponse)
def check_risk_budget(strategy_id: int, db: Session = Depends(get_db)):
    """检查风险预算"""
    from app.services.risk_controller import RiskController
    
    controller = RiskController()
    result = controller.check_risk_budget(strategy_id, db)
    
    return APIResponse(data=result)


@router.get("/stress-test", response_model=APIResponse)
def run_stress_test(strategy_id: int, db: Session = Depends(get_db)):
    """运行压力测试"""
    from app.services.risk_controller import RiskController
    
    controller = RiskController()
    result = controller.run_stress_test(strategy_id, db)
    
    return APIResponse(data=result)


@router.post("/detect-anomalies", response_model=APIResponse)
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


@router.post("/trigger-anomaly-review", response_model=APIResponse)
def trigger_anomaly_review(strategy_id: int, anomaly_type: str, db: Session = Depends(get_db)):
    """异常触发复盘"""
    from app.services.review_service import ReviewService
    
    svc = ReviewService()
    
    anomalies = svc.detect_anomalies(strategy_id, db)
    target_anomaly = None
    for a in anomalies:
        if a["type"] == anomaly_type:
            target_anomaly = a
            break
    
    if not target_anomaly:
        return APIResponse(message="未找到指定类型的异常")
    
    result = svc.trigger_anomaly_review(strategy_id, target_anomaly, db)
    
    return APIResponse(message="异常复盘完成", data=result)


@router.post("/suggest-parameter-adjustments", response_model=APIResponse)
def suggest_parameter_adjustments(strategy_id: int, db: Session = Depends(get_db)):
    """建议参数调整"""
    from app.services.review_service import ReviewService
    
    svc = ReviewService()
    suggestions = svc.suggest_parameter_adjustments(strategy_id, db)
    
    return APIResponse(data=suggestions)


@router.post("/compare-periods", response_model=APIResponse)
def compare_periods(
    strategy_id: int,
    period1_start: date,
    period1_end: date,
    period2_start: date,
    period2_end: date,
    db: Session = Depends(get_db)
):
    """跨周期对比分析"""
    from app.services.review_service import ReviewService
    
    svc = ReviewService()
    comparison = svc.compare_periods(
        strategy_id, period1_start, period1_end, period2_start, period2_end, db
    )
    
    return APIResponse(data=comparison)


@router.post("/enhanced-market-analysis", response_model=APIResponse)
def enhanced_market_analysis(strategy_id: int, db: Session = Depends(get_db)):
    """增强版市场分析"""
    from app.services.auto_analysis_service import AutoAnalysisService
    
    svc = AutoAnalysisService()
    result = svc.analyze_market(strategy_id, date.today(), db)
    
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return APIResponse(message="增强版市场分析完成", data=result)


@router.post("/full-risk-check", response_model=APIResponse)
def full_risk_check(strategy_id: int, db: Session = Depends(get_db)):
    """全面风险检查"""
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