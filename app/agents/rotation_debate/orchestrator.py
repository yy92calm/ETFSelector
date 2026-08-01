import logging
from typing import Dict, List
from app.agents.rotation_debate.momentum_advocate import MomentumAdvocate
from app.agents.rotation_debate.stability_advocate import StabilityAdvocate
from app.agents.rotation_debate.rotation_judge import RotationJudge

logger = logging.getLogger(__name__)


class RotationDebateOrchestrator:
    """轮动辩论编排：动量派 vs 稳定派 → 裁决官"""

    def __init__(self):
        self.momentum = MomentumAdvocate()
        self.stability = StabilityAdvocate()
        self.judge = RotationJudge()

    def debate(self, holdings: List[Dict], candidates: List[Dict], macro_context: str = "") -> Dict:
        logger.info("[RotationDebate] 开始轮动辩论")

        momentum_opinion = self.momentum.analyze(holdings, candidates, macro_context)
        if "error" in momentum_opinion:
            logger.warning(f"[RotationDebate] 动量派失败: {momentum_opinion.get('error')}")

        stability_opinion = self.stability.analyze(holdings, candidates, macro_context)
        if "error" in stability_opinion:
            logger.warning(f"[RotationDebate] 稳定派失败: {stability_opinion.get('error')}")

        final = self.judge.analyze(momentum_opinion, stability_opinion, holdings, candidates)

        if "error" in final:
            logger.warning(f"[RotationDebate] 裁决失败: {final.get('error')}")
            return {"decision": "hold", "final_swaps": [], "reason": "辩论异常，维持持仓"}

        logger.info(f"[RotationDebate] 裁决: {final.get('decision')} | {final.get('summary', '')[:60]}")
        return final
