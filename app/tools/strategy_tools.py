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


@tool(name="suggest_allocation_change", description="提交组合调仓建议（仅记录，不改变配置）。实际换仓由轮动通道统一执行：若提供 swaps 显式替换提案并校验通过（持仓池/最短持有期/禁入名单/每次≤2只/单只≤40%）则直接执行；否则进入辩论或驳回并回执。新增或删除标的也用本工具。")
def suggest_allocation_change(db: Session, strategy_id: int, new_allocation: dict,
                              reason: str = "", swaps: list = None) -> dict:
    """记录调仓建议（LLM 只建议，唯一换仓通道是轮动）

    Args:
    strategy_id: 策略ID
    new_allocation: 建议配置 {ETF代码: 比例}，总和必须为1.0
    reason: 建议理由（说明引用了哪些依据）
    swaps: 显式替换提案 [{remove: 换出代码, add: 换入代码, weight: 可选权重, reason: 可选理由}]，最多2对；提供后可被通道校验并直接执行
    """
    from app.services.allocation_suggestion_service import get_allocation_suggestion_service

    # 说明：本工具只写建议记录（不触碰 allocation_config/pending_allocation），
    # 因此显式声明为只读风险——真正的资金动作只有一个入口：轮动通道 execute_rotation。
    return get_allocation_suggestion_service().create(
        db, strategy_id, new_allocation, reason=reason, source="agentloop", swaps=swaps,
    )


@tool(name="get_allocation_suggestions", description="查询指定策略的调仓建议记录（含待裁决/已采纳/已驳回及裁决说明），用于避免重复建议与复盘建议采纳情况")
def get_allocation_suggestions(db: Session, strategy_id: int, limit: int = 10) -> dict:
    """调仓建议记录（只读）

    Args:
    strategy_id: 策略ID
    limit: 返回条数（默认10）
    """
    from app.models.allocation_suggestion import AllocationSuggestion

    rows = (
        db.query(AllocationSuggestion)
        .filter(AllocationSuggestion.strategy_id == strategy_id)
        .order_by(AllocationSuggestion.created_at.desc())
        .limit(max(1, min(limit, 50)))
        .all()
    )
    return {
        "strategy_id": strategy_id,
        "suggestions": [{
            "id": r.id,
            "status": r.status,
            "source": r.source,
            "suggested_allocation": r.suggested_allocation,
            "reason": r.reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "decided_note": r.decided_note,
        } for r in rows],
        "total": len(rows),
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


@tool(name="get_rule_suggestion", description="获取策略在当前市场状态下的规则建议配置（历史规则统计→配置映射，与规则驱动回测同源）。返回规则来源、样本天数、建议配置及与当前配置的偏离，是调仓决策的依据之一")
def get_rule_suggestion(db: Session, strategy_id: int) -> dict:
    from sqlalchemy import func
    from app.models.etf import ETFDailyIndicator
    from app.models.strategy import Strategy
    from app.services.rule_engine import get_rule_engine

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    scan_date = db.query(func.max(ETFDailyIndicator.trade_date)).scalar()
    if not scan_date:
        return {"error": "无量化指标数据，无法给出规则建议"}

    return get_rule_engine().get_rule_suggestion(
        scan_date, db,
        strategy_id=strategy_id,
        base_allocation=strategy.allocation_config or {},
    )


@tool(name="get_strategy_evidence", description="获取策略的四类决策依据（行情评分与换仓差距/行业性价比/规则建议/相关舆情）及最近一次决策留痕。调仓前应查阅，确保决策有据可依")
def get_strategy_evidence(db: Session, strategy_id: int) -> dict:
    from app.models.strategy import Strategy
    from app.services.strategy_evidence_service import get_strategy_evidence_service

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    evidence = get_strategy_evidence_service().get_evidence(strategy_id, db)
    if not evidence.get("exists"):
        return {"error": f"策略 {strategy_id} 依据不存在"}
    return evidence
