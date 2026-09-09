"""因子表现与失败模式API"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse
from app.services.factor_performance_service import get_factor_performance_service
from app.services.failure_mode_service import get_failure_mode_service

router = APIRouter(prefix="/api/factors", tags=["因子表现"])


@router.get("/ic-history", response_model=APIResponse)
def get_ic_history(days: int = 30, db: Session = Depends(get_db)):
    """获取近期各因子每日IC历史"""
    svc = get_factor_performance_service()
    return APIResponse(data={"history": svc.get_ic_history(db, days)})


@router.get("/adaptive-weights", response_model=APIResponse)
def get_adaptive_weights(db: Session = Depends(get_db)):
    """获取当前自适应权重"""
    svc = get_factor_performance_service()
    weights = svc.get_adaptive_weights(db)
    return APIResponse(data={"adaptive_weights": weights, "default_weights": {
        "momentum": 0.35, "trend": 0.20, "volume": 0.15,
        "volatility": 0.15, "capital_flow": 0.15,
    }})


@router.post("/backfill", response_model=APIResponse)
def trigger_backfill(db: Session = Depends(get_db)):
    """手动触发因子未来收益回填"""
    svc = get_factor_performance_service()
    filled = svc.backfill_forward_returns(db)
    return APIResponse(message=f"回填{filled}条", data={"filled": filled})


@router.get("/etf", response_model=APIResponse)
def get_etf_factor_profile(etf_code: str, db: Session = Depends(get_db)):
    """单ETF因子画像：最新交易日各因子得分与综合排名"""
    from app.models.etf import ETFDailyIndicator
    from app.models.factor_performance import FactorPerformance

    latest = (
        db.query(FactorPerformance.trade_date)
        .filter(FactorPerformance.etf_code == etf_code)
        .order_by(FactorPerformance.trade_date.desc())
        .first()
    )
    if not latest:
        return APIResponse(data=None)
    trade_date = latest[0]

    rows = (
        db.query(FactorPerformance)
        .filter(
            FactorPerformance.etf_code == etf_code,
            FactorPerformance.trade_date == trade_date,
        )
        .all()
    )
    factor_scores = {r.factor_name: r.factor_value for r in rows}

    ind = (
        db.query(ETFDailyIndicator)
        .filter(
            ETFDailyIndicator.etf_code == etf_code,
            ETFDailyIndicator.trade_date == trade_date,
        )
        .first()
    )

    return APIResponse(data={
        "etf_code": etf_code,
        "trade_date": trade_date.isoformat(),
        "composite_score": ind.composite_score if ind else None,
        "rank": ind.rank_in_market if ind else None,
        "factor_scores": factor_scores,
    })


@router.get("/failure-modes", response_model=APIResponse)
def get_failure_modes(limit: int = 20, db: Session = Depends(get_db)):
    """获取活跃失败模式"""
    svc = get_failure_mode_service()
    modes = svc.get_active_failure_modes(db, limit=limit)
    banned = svc.get_banned_codes(db)
    return APIResponse(data={"failure_modes": modes, "banned_codes": banned})
