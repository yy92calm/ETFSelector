import logging
from typing import Dict
from sqlalchemy.orm import Session

from app.agents.risk_agents.aggressive_risk import AggressiveRiskAgent
from app.agents.risk_agents.conservative_risk import ConservativeRiskAgent
from app.agents.risk_agents.neutral_risk import NeutralRiskAgent
from app.agents.risk_agents.risk_manager import RiskManager
from app.services.risk_controller import RiskController

logger = logging.getLogger(__name__)


class RiskDebateOrchestrator:
    def __init__(self):
        self.aggressive = AggressiveRiskAgent()
        self.conservative = ConservativeRiskAgent()
        self.neutral = NeutralRiskAgent()
        self.manager = RiskManager()

    def evaluate(self, strategy_id: int, market_regime: str, db: Session) -> Dict:
        logger.info(f"[RiskDebate] 策略{strategy_id} 开始三方风控辩论")

        ctrl = RiskController()
        risk_data = {
            "circuit_breaker": ctrl.check_circuit_breaker(strategy_id, db),
            "drawdown": ctrl.apply_drawdown_protection(strategy_id, db),
            "risk_budget": ctrl.check_risk_budget(strategy_id, db),
            "stress_test": ctrl.run_stress_test(strategy_id, db),
            "market_regime": market_regime,
        }

        circuit = risk_data["circuit_breaker"]
        if circuit.get("status") == "triggered":
            logger.warning(f"[RiskDebate] 熔断触发，跳过辩论直接拦截")
            return {
                "stage": "risk_check",
                "status": "triggered",
                "reason": f"熔断触发: {circuit.get('reason', '')}",
                "action": circuit.get("action", "pause_strategy"),
            }

        drawdown = risk_data["drawdown"]
        if drawdown.get("status") == "critical":
            logger.warning(f"[RiskDebate] 回撤临界，跳过辩论直接减仓")
            return {
                "stage": "risk_check",
                "status": "critical",
                "reason": f"回撤临界: {drawdown.get('message', '')}",
                "action": "reduce_position",
                "suggested_allocation": drawdown.get("suggested_allocation"),
            }

        ag_report = self.aggressive.analyze(risk_data)
        co_report = self.conservative.analyze(risk_data)
        ne_report = self.neutral.analyze(risk_data)

        final = self.manager.analyze(ag_report, co_report, ne_report)

        if "error" in final:
            logger.warning(f"[RiskDebate] 风控主管裁决失败，回退常规风控")
            return {
                "stage": "risk_check",
                "status": drawdown.get("status", "passed"),
                "reason": drawdown.get("message", ""),
                "debate_result": final,
            }

        action = final.get("final_suggested_action", "proceed")
        risk_level = final.get("final_risk_level", "low")

        if action == "stop":
            return {
                "stage": "risk_check",
                "status": "triggered",
                "reason": f"风控辩论结论: {final.get('reason', '')}",
                "action": "pause_strategy",
                "debate_result": final,
            }

        if action == "reduce":
            allocation = db.query(type("M", (), {"allocation_config": {}})).first()
            return {
                "stage": "risk_check",
                "status": "critical",
                "reason": f"风控辩论建议减仓: {final.get('reason', '')}",
                "action": "reduce_position",
                "suggested_allocation": {},
                "debate_result": final,
            }

        if drawdown.get("status") == "warning":
            logger.warning(f"[RiskDebate] 回撤预警，但风控辩论允许继续")
            return {"stage": "risk_check", "status": "passed", "debate_result": final}

        return {"stage": "risk_check", "status": "passed", "debate_result": final}
