"""组合管理工具 - 持仓、交易、再平衡"""

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.tools.registry import tool

logger = logging.getLogger(__name__)


@tool(name="get_portfolio_status", description="获取指定策略的当前持仓和最新资产快照")
def get_portfolio_status(db: Session, strategy_id: int) -> dict:
    from app.services.portfolio_service import get_portfolio_service

    svc = get_portfolio_service()
    holdings = svc.get_holdings(strategy_id, db)
    history = svc.get_portfolio_history(strategy_id, db)

    holdings_data = [
        {
            "etf_code": h.etf_code,
            "quantity": h.quantity,
            "avg_cost": h.avg_cost,
            "current_price": h.current_price,
            "market_value": h.market_value,
        }
        for h in holdings
    ]

    latest_snapshot = history[-1] if history else None
    snapshot_data = None
    if latest_snapshot:
        snapshot_data = {
            "trade_date": latest_snapshot.trade_date.isoformat(),
            "total_asset": latest_snapshot.total_asset,
            "cash": latest_snapshot.cash,
            "market_value": latest_snapshot.market_value,
            "profit": latest_snapshot.profit,
            "profit_pct": latest_snapshot.profit_pct,
        }

    return {
        "strategy_id": strategy_id,
        "holdings": holdings_data,
        "latest_snapshot": snapshot_data,
        "total_holdings": len(holdings_data),
    }


@tool(name="get_trade_history", description="获取指定策略的交易记录（最近N条）")
def get_trade_history(db: Session, strategy_id: int, limit: int = 20) -> dict:
    from app.services.portfolio_service import get_portfolio_service

    svc = get_portfolio_service()
    trades = svc.get_trades(strategy_id, db)

    data = [
        {
            "trade_date": t.trade_date.isoformat(),
            "etf_code": t.etf_code,
            "direction": t.direction,
            "price": t.price,
            "quantity": t.quantity,
            "amount": t.amount,
            "reason": t.reason,
        }
        for t in trades[:limit]
    ]

    return {"strategy_id": strategy_id, "total": len(trades), "recent_trades": data}


@tool(name="execute_rebalance", description="手动触发指定策略的再平衡检查与执行（基于当前市场数据）")
def execute_rebalance(db: Session, strategy_id: int) -> dict:
    from app.models.strategy import Strategy
    from app.services.portfolio_service import get_portfolio_service

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    svc = get_portfolio_service()
    try:
        svc.run_strategy_for_date(strategy, date.today(), db)
        return {
            "success": True,
            "strategy_id": strategy_id,
            "message": f"策略 {strategy_id} 再平衡执行完成",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@tool(name="get_portfolio_performance", description="获取指定策略的收益表现（资产曲线摘要）")
def get_portfolio_performance(db: Session, strategy_id: int) -> dict:
    from app.services.portfolio_service import get_portfolio_service

    svc = get_portfolio_service()
    history = svc.get_portfolio_history(strategy_id, db)

    if not history:
        return {"strategy_id": strategy_id, "message": "暂无快照数据"}

    first = history[0]
    latest = history[-1]

    # 计算最大回撤
    peak = 0
    max_dd = 0
    for snap in history:
        if snap.total_asset > peak:
            peak = snap.total_asset
        dd = (peak - snap.total_asset) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "strategy_id": strategy_id,
        "start_date": first.trade_date.isoformat(),
        "latest_date": latest.trade_date.isoformat(),
        "initial_asset": first.total_asset,
        "current_asset": latest.total_asset,
        "total_profit": latest.profit,
        "total_profit_pct": latest.profit_pct,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "snapshot_count": len(history),
        "recent_5": [
            {"date": s.trade_date.isoformat(), "total_asset": s.total_asset, "profit_pct": s.profit_pct}
            for s in history[-5:]
        ],
    }


@tool(name="evaluate_strategy_health", description="评估所有活跃策略的健康状态，返回每个策略的收益、回撤、运行天数，并标记表现不佳的策略（用于生命周期管理决策）")
def evaluate_strategy_health(db: Session) -> dict:
    from app.models.strategy import Strategy
    from app.services.portfolio_service import get_portfolio_service

    strategies = db.query(Strategy).filter(Strategy.status == "active").all()
    if not strategies:
        return {"total": 0, "message": "无活跃策略", "strategies": []}

    svc = get_portfolio_service()
    results = []

    for s in strategies:
        history = svc.get_portfolio_history(s.id, db)
        entry = {
            "strategy_id": s.id,
            "name": s.name,
            "strategy_source": s.strategy_source,
            "auto_status": s.auto_strategy_status,
            "allocation": s.allocation_config,
        }

        if not history:
            entry["status"] = "no_data"
            entry["health"] = "unknown"
            results.append(entry)
            continue

        first = history[0]
        latest = history[-1]
        running_days = (latest.trade_date - first.trade_date).days if latest.trade_date and first.trade_date else 0

        # 计算最大回撤
        peak = 0
        max_dd = 0
        for snap in history:
            if snap.total_asset > peak:
                peak = snap.total_asset
            dd = (peak - snap.total_asset) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # 近7天收益
        recent_7d_pct = None
        if len(history) >= 7:
            recent_7d_pct = round(
                (latest.total_asset - history[-7].total_asset) / history[-7].total_asset * 100, 2
            )

        entry.update({
            "running_days": running_days,
            "total_profit_pct": latest.profit_pct,
            "max_drawdown_pct": round(max_dd * 100, 2),
            "recent_7d_pct": recent_7d_pct,
            "current_asset": latest.total_asset,
        })

        # 健康度判断
        if max_dd > 0.15:
            entry["health"] = "critical"
            entry["warning"] = f"最大回撤{max_dd*100:.1f}%超过15%阈值"
        elif latest.profit_pct < -10:
            entry["health"] = "poor"
            entry["warning"] = f"累计亏损{abs(latest.profit_pct):.1f}%"
        elif running_days > 14 and (recent_7d_pct is not None and recent_7d_pct < -5):
            entry["health"] = "declining"
            entry["warning"] = f"近7天下跌{abs(recent_7d_pct):.1f}%"
        else:
            entry["health"] = "healthy"

        results.append(entry)

    # 统计
    unhealthy = [r for r in results if r.get("health") in ("critical", "poor", "declining")]

    return {
        "total": len(results),
        "healthy_count": len(results) - len(unhealthy),
        "unhealthy_count": len(unhealthy),
        "unhealthy_strategies": [{"id": r["strategy_id"], "name": r["name"], "warning": r.get("warning")} for r in unhealthy],
        "strategies": results,
    }
