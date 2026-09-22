import logging
from typing import Dict, List, Optional

from app.agents.rotation_debate.momentum_advocate import MomentumAdvocate
from app.agents.rotation_debate.stability_advocate import StabilityAdvocate
from app.agents.rotation_debate.rotation_judge import RotationJudge

logger = logging.getLogger(__name__)


def format_rule_context(rule_signal: Optional[Dict]) -> str:
    """把规则建议渲染成辩论材料中的「规则参考」文本段"""
    if not rule_signal:
        return "无规则参考（该策略暂无历史规则样本）"

    alloc = rule_signal.get("suggested_allocation") or {}
    alloc_str = "、".join(
        f"{code} {w * 100:.0f}%"
        for code, w in sorted(alloc.items(), key=lambda x: -x[1])
        if w >= 0.005
    ) or "无"
    deviation = [
        d for d in (rule_signal.get("deviation") or [])
        if abs(d.get("delta") or 0) >= 0.01
    ]
    deviation_str = "、".join(
        f"{d['etf_code']} {d['delta'] * 100:+.0f}%" for d in deviation
    ) or "与当前配置基本一致"

    lines = [
        f"市场状态：{rule_signal.get('regime_label', '-')}（{rule_signal.get('regime', '-')}）",
        f"规则来源：{rule_signal.get('rule_source_label', '-')}"
        + (f"（样本{rule_signal['sample_count']}天）" if rule_signal.get("sample_count") else ""),
        f"规则建议配置：{alloc_str}",
        f"与当前配置偏离：{deviation_str}",
    ]
    if rule_signal.get("note"):
        lines.append(f"规则说明：{rule_signal['note']}")
    return "\n".join(lines)


class RotationDebateOrchestrator:
    """轮动辩论编排：动量派 vs 稳定派 → 裁决官"""

    def __init__(self):
        self.momentum = MomentumAdvocate()
        self.stability = StabilityAdvocate()
        self.judge = RotationJudge()

    def debate(self, holdings: List[Dict], candidates: List[Dict],
               macro_context: str = "", rule_signal: Optional[Dict] = None) -> Dict:
        logger.info("[RotationDebate] 开始轮动辩论")
        rule_context = format_rule_context(rule_signal)

        momentum_opinion = self.momentum.analyze(holdings, candidates, macro_context, rule_context)
        if "error" in momentum_opinion:
            logger.warning(f"[RotationDebate] 动量派失败: {momentum_opinion.get('error')}")

        stability_opinion = self.stability.analyze(holdings, candidates, macro_context, rule_context)
        if "error" in stability_opinion:
            logger.warning(f"[RotationDebate] 稳定派失败: {stability_opinion.get('error')}")

        final = self.judge.analyze(momentum_opinion, stability_opinion, holdings, candidates,
                                   rule_context)

        if "error" in final:
            logger.warning(f"[RotationDebate] 裁决失败: {final.get('error')}")
            return {"decision": "hold", "final_swaps": [], "reason": "辩论异常，维持持仓"}

        logger.info(f"[RotationDebate] 裁决: {final.get('decision')} | {final.get('summary', '')[:60]}")
        return final