"""风控工具 - 风险仪表盘、压力测试、熔断检查"""

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.tools.registry import tool

logger = logging.getLogger(__name__)


@tool(name="get_risk_dashboard", description="获取指定策略的完整风险仪表盘，包含熔断状态、回撤保护、风险预算、压力测试摘要")
def get_risk_dashboard(db: Session, strategy_id: int) -> dict:
    from app.services.risk_controller import RiskController

    ctrl = RiskController()
    return ctrl.get_risk_dashboard(strategy_id, db)


@tool(name="run_stress_test", description="对指定策略运行压力测试，模拟市场暴跌、金融危机等极端场景")
def run_stress_test(db: Session, strategy_id: int) -> dict:
    from app.services.risk_controller import RiskController

    ctrl = RiskController()
    return ctrl.run_stress_test(strategy_id, db)


@tool(name="check_circuit_breaker", description="检查指定策略是否触发熔断条件（连续亏损、单日暴跌、累计亏损）")
def check_circuit_breaker(db: Session, strategy_id: int) -> dict:
    from app.services.risk_controller import RiskController

    ctrl = RiskController()
    return ctrl.check_circuit_breaker(strategy_id, db)


@tool(name="pause_strategy", description="暂停指定策略的自动执行")
def pause_strategy(db: Session, strategy_id: int, reason: str = "手动暂停") -> dict:
    from app.models.strategy import Strategy

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    strategy.auto_strategy_status = "paused"
    strategy.paused_reason = reason
    strategy.paused_date = date.today()
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
    db.commit()

    return {
        "success": True,
        "strategy_id": strategy_id,
        "status": "running",
        "message": f"策略 {strategy_id} 已恢复运行",
    }
