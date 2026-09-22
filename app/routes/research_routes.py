"""盈利-估值性价比研究API"""

import logging
import threading

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse
from app.services.fundamental_data_service import get_fundamental_data_service
from app.services.value_model_service import get_value_model_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["性价比研究"])


@router.get("/industry-ranking", response_model=APIResponse)
def get_industry_ranking(days: int = 1, db: Session = Depends(get_db)):
    """行业盈利-估值性价比排名（最新评分日）"""
    rows = get_value_model_service().get_industry_ranking(db, days)
    return APIResponse(data={
        "industries": [{
            "trade_date": r.trade_date.isoformat(),
            "industry": r.industry,
            "sample_count": r.sample_count,
            "median_pe": r.median_pe,
            "median_growth": r.median_growth,
            "peg": r.peg,
            "pe_percentile": r.pe_percentile,
            "trend_momentum": r.trend_momentum,
            "score": r.score,
            "rank": r.rank,
        } for r in rows]
    })


@router.get("/stock-picks", response_model=APIResponse)
def get_stock_picks(top_n: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    """个股安全边际筛选：盈利正增长 + PEG 达标，按安全边际得分排序"""
    picks = get_value_model_service().get_stock_picks(db, top_n=top_n)
    return APIResponse(data={"picks": picks, "total": len(picks)})


@router.get("/strategy-mapping", response_model=APIResponse)
def get_strategy_mapping(strategy_id: int = 0, db: Session = Depends(get_db)):
    """本策略标的池→行业性价比映射（研究视图用）：池=当前配置∪待生效配置，无匹配时返回空"""
    if not strategy_id:
        return APIResponse(data={"etfs": [], "industries": {}})

    from app.models.etf import ETFBasic
    from app.services.portfolio_service import get_portfolio_service

    universe = get_portfolio_service().get_strategy_universe(strategy_id, db)
    codes = sorted(universe["pool"] | universe["holding_codes"])
    if not codes:
        return APIResponse(data={"etfs": [], "industries": {}})

    names = {
        b.etf_code: b.etf_name
        for b in db.query(ETFBasic).filter(ETFBasic.etf_code.in_(codes)).all()
    }
    signals = get_value_model_service().get_etf_industry_signals(
        db, {c: names.get(c) for c in codes}
    )

    etfs = []
    industries: dict = {}
    for code in codes:
        sig = signals.get(code) or {}
        h = universe["holdings"].get(code)
        total_asset = universe["total_asset"]
        etfs.append({
            "etf_code": code,
            "etf_name": names.get(code, ""),
            "is_holding": h is not None,
            "holding_pct": round(h.market_value / total_asset * 100, 1) if (h and total_asset > 0) else None,
            "industry": sig.get("industry"),
            "score": sig.get("score"),
            "rank": sig.get("rank"),
            "total": sig.get("total"),
            "as_of": sig.get("as_of"),
        })
        if sig.get("industry"):
            industries.setdefault(sig["industry"], []).append(code)

    return APIResponse(data={"etfs": etfs, "industries": industries})


@router.post("/backfill", response_model=APIResponse)
def trigger_backfill(years: int = Query(3, ge=1, le=5), db: Session = Depends(get_db)):
    """后台回填池内个股历史估值 + 增速 + 行业（首次上线/年度刷新用，耗时数分钟）"""
    from app.db.database import SessionLocal

    def _run():
        thread_db = SessionLocal()
        try:
            result = get_fundamental_data_service().sync_fundamentals(
                thread_db, days_back=years * 365,
                refresh_growth=True, refresh_industry=True,
            )
            logger.info(f"[Research] 历史回填完成: {result}")
            n = get_value_model_service().compute_industry_scores(thread_db)
            logger.info(f"[Research] 行业评分完成: {n} 个行业")
        except Exception as e:
            logger.error(f"[Research] 历史回填失败: {e}")
        finally:
            thread_db.close()

    threading.Thread(target=_run, daemon=True, name="research-backfill").start()
    return APIResponse(message=f"历史回填已后台启动（约{years}年，耗时数分钟，可稍后查看研究视图）")
