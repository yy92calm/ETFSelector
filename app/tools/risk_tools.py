"""风控工具 - 风险仪表盘、压力测试、熔断检查、分散度与在险价值"""

import logging
from datetime import date
from typing import List, Optional, TypedDict

from sqlalchemy.orm import Session

from app.tools.registry import tool

logger = logging.getLogger(__name__)


class _StressScenarioRequired(TypedDict):
    """自定义压力场景的必填项"""
    shock: float


class StressScenario(_StressScenarioRequired, total=False):
    """自定义跌幅场景（只用于生成工具 schema，运行时就是普通 dict）"""
    name: str
    description: str


class _WhatIfSwapRequired(TypedDict):
    """假设替换的必填项"""
    remove: str
    add: str


class WhatIfSwap(_WhatIfSwapRequired, total=False):
    """一对假设换仓（换出 remove、换入 add，可选权重），只用于算风险，不会写库"""
    weight: float


@tool(name="get_risk_dashboard", description="获取指定策略的完整风险仪表盘，包含熔断状态、回撤保护、风险预算、压力测试摘要")
def get_risk_dashboard(db: Session, strategy_id: int) -> dict:
    from app.services.risk_controller import RiskController

    ctrl = RiskController()
    return ctrl.get_risk_dashboard(strategy_id, db)


@tool(name="get_portfolio_correlation", description="计算组合持仓近N个交易日日收益的相关系数矩阵与平均相关度，判断配置是「真分散」还是同涨同跌（同涨同跌=分散无效）")
def get_portfolio_correlation(db: Session, strategy_id: int, window: int = 60) -> dict:
    """持仓相关性矩阵与分散度

    Args:
    strategy_id: 策略ID
    window: 回看交易日数（默认60，样本不足20天时返回不可用说明）
    """
    from app.services.risk_controller import RiskController

    return RiskController().get_diversification(strategy_id, db, window=window)


@tool(name="get_portfolio_var", description="历史模拟法计算组合在险价值VaR：用当前配置权重合成的历史日收益序列取分位损失，给出置信度下的单日最大预期损失与尾部均值")
def get_portfolio_var(db: Session, strategy_id: int, horizon: int = 1,
                      confidence: float = 0.95) -> dict:
    """组合在险价值（VaR）

    Args:
    strategy_id: 策略ID
    horizon: 持有天数（默认1，多日按√t时间缩放）
    confidence: 置信度（0.5~0.999，默认0.95）
    """
    from app.services.risk_controller import RiskController

    return RiskController().get_var(strategy_id, db, horizon=horizon, confidence=confidence)


@tool(name="run_stress_test", description="对指定策略运行压力测试，模拟市场暴跌、金融危机等极端场景；可用 scenario 指定自定义跌幅场景")
def run_stress_test(db: Session, strategy_id: int,
                    scenario: Optional[StressScenario] = None) -> dict:
    """压力测试

    Args:
    strategy_id: 策略ID
    scenario: 自定义场景 {shock: 跌幅(如 -0.15 或 0.15), name: 可选场景名, description: 可选说明}，提供时替换预设四场景
    """
    from app.services.risk_controller import RiskController

    return RiskController().run_stress_test(strategy_id, db, scenario=scenario)


@tool(name="check_circuit_breaker", description="检查指定策略是否触发熔断条件（连续亏损、单日暴跌、累计亏损）；可用 what_if_swaps 模拟「若执行这些换仓后」的仓位与尾部风险是否越线（只算不改库）")
def check_circuit_breaker(db: Session, strategy_id: int,
                          what_if_swaps: Optional[List[WhatIfSwap]] = None) -> dict:
    """熔断检查（支持换仓 what-if）

    Args:
    strategy_id: 策略ID
    what_if_swaps: 假设替换列表 [{remove: 换出代码, add: 换入代码, weight: 可选权重}]，给出时返回 what_if 对照块
    """
    from app.services.risk_controller import RiskController

    return RiskController().check_circuit_breaker(strategy_id, db,
                                                  what_if_swaps=what_if_swaps)


@tool(name="pause_strategy", description="暂停指定策略的自动执行")
def pause_strategy(db: Session, strategy_id: int, reason: str = "手动暂停") -> dict:
    from app.models.strategy import Strategy

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    strategy.auto_strategy_status = "paused"
    strategy.paused_reason = reason
    strategy.paused_date = date.today()
    strategy.paused_cooldown_days = None   # 人工/LLM 暂停不自动恢复
    db.commit()

    return {
        "success": True,
        "strategy_id": strategy_id,
        "status": "paused",
        "reason": reason,
        "message": f"策略 {strategy_id} 已暂停: {reason}",
    }


@tool(name="resume_strategy", description="恢复指定策略的自动执行")
def resume_strategy(db: Session, strategy_id: int) -> dict:
    from app.models.strategy import Strategy

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    strategy.auto_strategy_status = "running"
    strategy.paused_reason = None
    strategy.paused_date = None
    strategy.paused_cooldown_days = None
    db.commit()

    return {
        "success": True,
        "strategy_id": strategy_id,
        "status": "running",
        "message": f"策略 {strategy_id} 已恢复运行",
    }


@tool(name="get_banned_codes", description="获取失败模式规避名单：反复出现在失败决策中的ETF代码及失败次数，这些代码在AI建议配置中会被标记警告")
def get_banned_codes(db: Session) -> dict:
    from app.services.failure_mode_service import get_failure_mode_service

    banned = get_failure_mode_service().get_banned_codes(db)
    return {"banned_codes": banned, "total": len(banned)}
