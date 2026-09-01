"""组合/持仓/交易记录API"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date

from app.db.database import get_db
from app.schemas.schemas import APIResponse
from app.services.portfolio_service import get_portfolio_service
from app.services.strategy_service import get_strategy_service

router = APIRouter(prefix="/api/portfolio", tags=["组合管理"])


@router.get("/{strategy_id}/history", response_model=APIResponse)
def get_portfolio_history(
    strategy_id: int,
    start_date: Optional[date] = Query(None, description="起始日期，过滤该日期之后的快照"),
    db: Session = Depends(get_db)
):
    """获取策略的每日资产快照（含每日持仓记录）"""
    from app.models.portfolio import HoldingSnapshot

    svc = get_portfolio_service()
    snapshots = svc.get_portfolio_history(strategy_id, db, start_date)

    # 每日持仓记录按日期分组
    hs_query = db.query(HoldingSnapshot).filter(HoldingSnapshot.strategy_id == strategy_id)
    if start_date:
        hs_query = hs_query.filter(HoldingSnapshot.trade_date >= start_date)
    holdings_by_date = {}
    for h in hs_query.all():
        holdings_by_date.setdefault(h.trade_date, []).append({
            "etf_code": h.etf_code,
            "quantity": h.quantity,
            "price": h.price,
            "market_value": h.market_value,
        })

    return APIResponse(data={
        "snapshots": [{
            "trade_date": s.trade_date.isoformat(),
            "total_asset": s.total_asset,
            "cash": s.cash,
            "market_value": s.market_value,
            "profit": s.profit,
            "profit_pct": s.profit_pct,
            "holdings": holdings_by_date.get(s.trade_date, []),
        } for s in snapshots],
    })


@router.get("/{strategy_id}/holdings", response_model=APIResponse)
def get_holdings(strategy_id: int, db: Session = Depends(get_db)):
    """获取策略当前持仓

    响应含 summary（最新资产快照口径）：
    - total_asset: 总资产（持仓市值+现金）
    - nav: 资产净值（单位净值，total_asset/initial_capital，期初1.0000）
    - cash: 现金；market_value: 持仓市值；as_of: 快照日期
    无快照（尚未建仓）时给初始口径：总资产=现金=初始资金，净值1.0000
    """
    from app.models.portfolio import PortfolioSnapshot
    from app.models.strategy import Strategy
    from sqlalchemy import func

    svc = get_portfolio_service()
    holdings = svc.get_holdings(strategy_id, db)

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    initial_capital = float(strategy.initial_capital) if strategy and strategy.initial_capital else 0.0

    latest = (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.strategy_id == strategy_id)
        .order_by(PortfolioSnapshot.trade_date.desc())
        .first()
    )
    if latest:
        summary = {
            "market_value": latest.market_value,
            "cash": latest.cash,
            "total_asset": latest.total_asset,
            "nav": round(latest.total_asset / initial_capital, 4) if initial_capital > 0 else None,
            "as_of": latest.trade_date.isoformat(),
        }
    else:
        summary = {
            "market_value": 0.0,
            "cash": initial_capital,
            "total_asset": initial_capital,
            "nav": 1.0 if initial_capital > 0 else None,
            "as_of": None,
        }

    return APIResponse(data={
        "summary": summary,
        "holdings": [{
            "etf_code": h.etf_code,
            "quantity": h.quantity,
            "avg_cost": h.avg_cost,
            "current_price": h.current_price,
            "market_value": h.market_value,
        } for h in holdings],
    })


@router.get("/{strategy_id}/trades", response_model=APIResponse)
def get_trades(strategy_id: int, db: Session = Depends(get_db)):
    """获取策略交易记录"""
    svc = get_portfolio_service()
    trades = svc.get_trades(strategy_id, db)
    return APIResponse(data={
        "trades": [{
            "trade_date": t.trade_date.isoformat(),
            "etf_code": t.etf_code,
            "direction": t.direction,
            "price": t.price,
            "quantity": t.quantity,
            "amount": t.amount,
            "reason": t.reason,
        } for t in trades],
    })


@router.post("/{strategy_id}/catch-up", response_model=APIResponse)
def catch_up_strategy(strategy_id: int, db: Session = Depends(get_db)):
    """
    补跑策略：从创建日起逐日执行到今天
    用于新创建策略后的首次运行
    """
    strategy_svc = get_strategy_service()
    strategy = strategy_svc.get_strategy(strategy_id, db)
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    portfolio_svc = get_portfolio_service()
    try:
        portfolio_svc.catch_up_strategy(strategy, db)
        return APIResponse(message="策略补跑完成")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"补跑失败: {str(e)}")
