"""策略操作工具 - 策略CRUD与回测"""

import logging
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.tools.registry import tool

logger = logging.getLogger(__name__)


@tool(name="list_strategies", description="列出所有策略，包含名称、类型、状态、配置比例等信息")
def list_strategies(db: Session) -> dict:
    from app.models.strategy import Strategy

    strategies = db.query(Strategy).order_by(Strategy.id.desc()).all()
    data = [
        {
            "id": s.id,
            "name": s.name,
            "strategy_type": s.strategy_type,
            "strategy_source": s.strategy_source,
        "status": s.status,
        "auto_strategy_status": s.auto_strategy_status,
        "allocation_config": s.allocation_config,
        "pending_allocation": s.pending_allocation,
        "rebalance_freq": s.rebalance_freq,
        "rebalance_threshold": s.rebalance_threshold,
        "initial_capital": s.initial_capital,
        "last_auto_analysis_date": s.last_auto_analysis_date.isoformat() if s.last_auto_analysis_date else None,
        "last_analysis_result": s.last_analysis_result,
        "enable_memory": s.enable_memory,
    }
        for s in strategies
    ]
    return {"total": len(data), "strategies": data}


@tool(name="create_strategy", description="创建新的ETF配置组合策略。allocation_config为ETF代码到比例的映射，比例总和必须为1.0")
def create_strategy(
    db: Session,
    name: str,
    allocation_config: dict,
    rebalance_freq: str = "quarterly",
    rebalance_threshold: float = 0.05,
    initial_capital: int = 100000,
    description: str = "",
) -> dict:
    from app.services.strategy_service import get_strategy_service

    svc = get_strategy_service()
    try:
        strategy = svc.create_custom_strategy(
            {
                "name": name,
                "allocation_config": allocation_config,
                "rebalance_freq": rebalance_freq,
                "rebalance_threshold": rebalance_threshold,
                "initial_capital": initial_capital,
                "description": description,
            },
            db,
        )
        return {
            "success": True,
            "strategy_id": strategy.id,
            "name": strategy.name,
            "allocation_config": strategy.allocation_config,
            "message": f"策略 '{strategy.name}' 创建成功",
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}


@tool(name="delete_strategy", description="删除指定策略及其所有关联数据（持仓、交易记录、快照、经验）。不可恢复，谨慎操作。")
def delete_strategy(db: Session, strategy_id: int) -> dict:
    from app.models.strategy import Strategy
    from app.models.portfolio import PortfolioSnapshot, TradeRecord, Holding
    from app.models.auto_strategy_log import AutoStrategyLog
    from app.models.experience import Experience

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    name = strategy.name
    db.query(PortfolioSnapshot).filter(PortfolioSnapshot.strategy_id == strategy_id).delete()
    db.query(TradeRecord).filter(TradeRecord.strategy_id == strategy_id).delete()
    db.query(Holding).filter(Holding.strategy_id == strategy_id).delete()
    db.query(AutoStrategyLog).filter(AutoStrategyLog.strategy_id == strategy_id).delete()
    db.query(Experience).filter(Experience.strategy_id == strategy_id).delete()
    db.delete(strategy)
    db.commit()

    return {
        "success": True,
        "strategy_id": strategy_id,
        "name": name,
        "message": f"策略 '{name}'(ID={strategy_id}) 已删除",
    }


@tool(name="add_etf_to_strategy", description="向指定策略添加一个ETF。新ETF会获得指定权重，其余ETF权重等比缩减以保持总和为1.0。持仓上限5只。运行中的策略下一交易日生效。")
def add_etf_to_strategy(db: Session, strategy_id: int, etf_code: str, weight: float = 0.2) -> dict:
    from app.models.strategy import Strategy
    from app.services.strategy_service import get_strategy_service

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    svc = get_strategy_service()
    alloc = svc.get_effective_target_allocation(strategy)
    if etf_code in alloc:
        return {"error": f"ETF {etf_code} 已在策略中，当前权重 {alloc[etf_code]}"}

    if len(alloc) >= 5:
        return {"error": f"策略持仓已达上限5只，需先移除其他ETF"}

    if weight <= 0 or weight >= 1:
        return {"error": f"权重必须在0到1之间，当前 {weight}"}

    remaining_weight = 1.0 - weight
    old_total = sum(alloc.values())
    if old_total > 0:
        alloc = {k: round(v * remaining_weight / old_total, 4) for k, v in alloc.items()}
    alloc[etf_code] = round(weight, 4)

    total = sum(alloc.values())
    if abs(total - 1.0) > 0.01:
        diff = 1.0 - total
        first_key = next(iter(alloc))
        alloc[first_key] = round(alloc[first_key] + diff, 4)

    old_alloc = svc.get_effective_target_allocation(strategy)
    try:
        mode = svc.stage_allocation_change(strategy, alloc, db)
    except ValueError as e:
        return {"error": str(e)}
    db.commit()

    effective_note = "下一交易日生效" if mode == "pending" else "立即生效"
    return {
        "success": True,
        "strategy_id": strategy_id,
        "etf_code": etf_code,
        "weight": alloc[etf_code],
        "old_allocation": old_alloc,
        "new_allocation": alloc,
        "holdings_count": len(alloc),
        "effective": mode,
        "message": f"已添加 {etf_code}（权重 {alloc[etf_code]*100:.1f}%），{effective_note}，当前持仓 {len(alloc)} 只",
    }


@tool(name="remove_etf_from_strategy", description="从指定策略移除一个ETF。其权重按比例分配给剩余ETF。至少保留1只。运行中的策略下一交易日生效。")
def remove_etf_from_strategy(db: Session, strategy_id: int, etf_code: str) -> dict:
    from app.models.strategy import Strategy
    from app.services.strategy_service import get_strategy_service

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    svc = get_strategy_service()
    alloc = svc.get_effective_target_allocation(strategy)
    if etf_code not in alloc:
        return {"error": f"ETF {etf_code} 不在策略中"}

    if len(alloc) <= 1:
        return {"error": "策略至少需要保留1只ETF"}

    removed_weight = alloc.pop(etf_code)
    remaining_total = sum(alloc.values())
    if remaining_total > 0:
        alloc = {k: round(v / remaining_total, 4) for k, v in alloc.items()}
        total = sum(alloc.values())
        if abs(total - 1.0) > 0.01:
            diff = 1.0 - total
            first_key = next(iter(alloc))
            alloc[first_key] = round(alloc[first_key] + diff, 4)

    try:
        mode = svc.stage_allocation_change(strategy, alloc, db)
    except ValueError as e:
        return {"error": str(e)}
    db.commit()

    effective_note = "下一交易日生效" if mode == "pending" else "立即生效"
    return {
        "success": True,
        "strategy_id": strategy_id,
        "removed_etf": etf_code,
        "removed_weight": removed_weight,
        "new_allocation": alloc,
        "holdings_count": len(alloc),
        "effective": mode,
        "message": f"已移除 {etf_code}（原权重 {removed_weight*100:.1f}%），{effective_note}",
    }


@tool(name="update_allocation", description="修改指定策略的ETF配置比例。new_allocation的比例总和必须为1.0。运行中的策略下一交易日生效，当日持仓与历史收益不受影响")
def update_allocation(db: Session, strategy_id: int, new_allocation: dict) -> dict:
    from app.models.strategy import Strategy
    from app.services.strategy_service import get_strategy_service

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    svc = get_strategy_service()
    old_allocation = svc.get_effective_target_allocation(strategy)
    try:
        mode = svc.stage_allocation_change(strategy, new_allocation, db)
    except ValueError as e:
        return {"error": str(e)}
    db.commit()

    if mode == "pending":
        message = "配置已提交，下一交易日按新配置执行交易，当日持仓与历史收益不受影响"
    else:
        message = "配置比例已更新（新策略立即生效）"

    return {
        "success": True,
        "strategy_id": strategy_id,
        "old_allocation": old_allocation,
        "new_allocation": new_allocation,
        "effective": mode,
        "message": message,
    }


@tool(name="run_backtest", description="对指定策略执行历史回测，返回收益率、最大回撤、夏普比率等指标")
def run_backtest(db: Session, strategy_id: int, start_date: str, end_date: str) -> dict:
    from app.models.strategy import Strategy
    from app.services.backtest_service import get_backtest_engine

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return {"error": "日期格式错误，请使用 YYYY-MM-DD"}

    engine = get_backtest_engine()
    try:
        result = engine.run(strategy, sd, ed, float(strategy.initial_capital), db)
        return {
            "strategy_id": strategy_id,
            "strategy_name": result.get("strategy_name"),
            "period": f"{start_date}~{end_date}",
            "initial_capital": result["initial_capital"],
            "final_asset": result["final_asset"],
            "total_return_pct": result["total_return_pct"],
            "max_drawdown_pct": result["max_drawdown_pct"],
            "sharpe_ratio": result.get("sharpe_ratio"),
            "rebalance_count": result.get("rebalance_count"),
            "win_rate": result.get("win_rate"),
            "time_period_returns": result.get("time_period_returns"),
        }
    except ValueError as e:
        return {"error": str(e)}


@tool(name="get_strategy_detail", description="获取单个策略的完整详情，包含最近AI分析结果")
def get_strategy_detail(db: Session, strategy_id: int) -> dict:
    from app.models.strategy import Strategy

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    return {
        "id": strategy.id,
        "name": strategy.name,
        "description": strategy.description,
        "strategy_type": strategy.strategy_type,
        "strategy_source": strategy.strategy_source,
        "status": strategy.status,
        "auto_strategy_status": strategy.auto_strategy_status,
        "allocation_config": strategy.allocation_config,
        "rebalance_freq": strategy.rebalance_freq,
        "rebalance_threshold": strategy.rebalance_threshold,
        "initial_capital": strategy.initial_capital,
        "last_auto_analysis_date": strategy.last_auto_analysis_date.isoformat() if strategy.last_auto_analysis_date else None,
        "last_analysis_result": strategy.last_analysis_result,
        "enable_memory": strategy.enable_memory,
    }
