import json
import logging
import re
from typing import Dict, List, Set

from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)

# 标的代码：6 位数字，前后不能再有数字（避免把 20261010 这类日期当标的）
_CODE_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")

# 一行材料里展示的因子（值按落库原值输出，不额外假定单位）
_MOMENTUM_KEYS = (("momentum_5d", "5日动量"), ("momentum_20d", "20日动量"))
_FACTOR_KEYS = (("trend_strength", "趋势"), ("volatility_20d", "波动"), ("vol_ratio", "量比"))


def _mentioned_codes(*texts: str) -> Set[str]:
    """从辩论意见/建议文本里提取被提及的标的代码"""
    codes: Set[str] = set()
    for text in texts:
        if text:
            codes.update(_CODE_RE.findall(text))
    return codes


def _format_etf_line(item: Dict, tag: str) -> str:
    """把一条标的压成一行：代码 名称 [标签] 综合分/排名 + 因子 + 板块/性价比信号"""
    parts = [str(item.get("etf_code"))]
    if item.get("etf_name"):
        parts.append(item["etf_name"])
    parts.append(f"[{tag}]")

    score = item.get("composite_score")
    if score is not None:
        parts.append(f"综合分{score:.1f}")
    if item.get("rank") is not None:
        parts.append(f"排名{item['rank']}")

    factors = []
    for key, label in _MOMENTUM_KEYS:
        value = item.get(key)
        if value is not None:
            factors.append(f"{label}{value:+.2f}")
    for key, label in _FACTOR_KEYS:
        value = item.get(key)
        if value is not None:
            factors.append(f"{label}{value:.2f}")
    if factors:
        parts.append(" ".join(factors))

    sector = item.get("sector") or {}
    if sector.get("sector"):
        text = f"板块:{sector['sector']}"
        if sector.get("score") is not None:
            text += f"评分{sector['score']}"
        if sector.get("signal"):
            text += f"({sector['signal']})"
        parts.append(text)

    value_sig = item.get("industry_value") or {}
    if value_sig.get("rank") is not None:
        total = value_sig.get("total")
        parts.append(f"行业性价比{value_sig.get('industry', '')}"
                     f"{value_sig['rank']}/{total if total is not None else '?'}")

    if item.get("from_suggestion"):
        parts.append("调仓建议标的")

    return "- " + " ".join(parts)


class RotationJudge(BaseAgent):
    name = "rotation_judge"

    PROMPT = """你是轮动决策的最终裁决官。你需要综合动量派和稳定派的观点，做出最终轮换决策。

## 动量派意见
{momentum_opinion}

## 稳定派意见
{stability_opinion}

## 决策材料（只列争议标的）
{diff_context}

## 规则参考（该策略在同类市场状态下的历史统计偏好）
{rule_context}

## 板块轮动参考（申万一级行业）
{sector_context}

## 舆情参考（市场情绪 + 涉本策略标的）
{sentiment_context}

## 调仓建议参考（AI 建议，无约束力）
{suggestion_context}

## 裁决规则
- 持仓总数必须≤5只
- 有进必有出（替换制）
- 每次最多替换2只
- 候选得分必须显著高于被替换者（差距≥5分）
- 如果两派意见一致，直接执行
- 如果两派分歧，倾向于不换（除非得分差距>10分）
- 宏观环境为衰退时，优先防御性标的
- 候选材料只列两派或调仓建议**提及过**的标的；未提及即未进入争议，不要从外部自行挑选换入标的
- industry_value 为个股聚合的行业盈利-估值性价比排名（rank 越小越好，total 为参评行业数）：候选动量强但所属行业排名末段（rank/total>0.8）时，换入需更强证据；该信号仅到板块层面为止，严禁输出任何个股判断
- 板块轮动参考为申万一级行业评分（≥67 超配 / ≤33 低配）：分数差距接近（<10 分）时优先换入超配板块候选、优先换出低配板块持仓；板块仅做分层参考，个券综合分仍是主依据
- 舆情参考：市场情绪负面偏空且涉本策略标的出现负面条目时，弱持仓优先处置；情绪正面偏多**不作为**追涨依据
- 调仓建议参考：AI 建议**无约束力**。标有「★ 可执行提案」的替换已通过硬约束校验（持仓池/最短持有期/禁入/数量与权重上限），
  若你认同其逻辑，请在 final_swaps 中**原样采纳**（remove/add 一致，必要时带 weight_suggestion）；不认同则在 summary 写明关键理由（会作为回执留痕）。
  标有「✖ 已驳回提案」的不要采纳（已在通道侧驳回）

输出JSON（不要包含其他文字）：
{{
  "decision": "rotate/hold",
  "final_swaps": [
    {{
      "remove": "换出ETF代码",
      "add": "换入ETF代码",
      "reason": "裁决理由",
      "weight_suggestion": 0.0-1.0
    }}
  ],
  "hold_list": ["维持的ETF代码"],
  "dissent_note": "对少数派意见的回应",
  "next_review_trigger": "下次提前复盘的触发条件",
  "summary": "一句话裁决总结"
}}

如果决定不换，final_swaps为空数组，decision为"hold"。规则参考仅为历史统计偏好：与当日数据冲突时以当日数据为准，但需在 dissent_note 中说明为何不采纳规则建议。"""

    def _build_diff_context(self, momentum_opinion: Dict, stability_opinion: Dict,
                            holdings: List[Dict], candidates: List[Dict],
                            suggestion_context: str = "") -> str:
        """裁决官输入去重：持仓全列（替换制基准），候选只留争议标的

        全量候选池对裁决是噪音——两派都没讨论过的标的不应被临时挑中；
        建议通道里的标的必须保留（裁决官要能原样采纳提案）。
        """
        holding_codes = {h.get("etf_code") for h in holdings}
        mentioned = _mentioned_codes(
            json.dumps(momentum_opinion, ensure_ascii=False),
            json.dumps(stability_opinion, ensure_ascii=False),
            suggestion_context,
        )

        blocks = []
        if holdings:
            blocks.append("### 当前持仓（全部）\n" + "\n".join(
                _format_etf_line(h, "持仓") for h in holdings))

        disputed = [c for c in candidates
                    if c.get("etf_code") in mentioned and c.get("etf_code") not in holding_codes]
        if disputed:
            blocks.append("### 争议候选（两派或建议提及）\n" + "\n".join(
                _format_etf_line(c, "候选") for c in disputed))
        else:
            blocks.append("### 争议候选\n两派与建议均未提及池外候选，未进入争议（按规则应维持持仓）")

        omitted = len(candidates) - len(disputed)
        if omitted > 0:
            blocks.append(f"（另有 {omitted} 只未被提及的候选已从材料中省略）")
        return "\n".join(blocks)

    def analyze(self, momentum_opinion: Dict, stability_opinion: Dict,
                holdings: list, candidates: list, rule_context: str = "",
                sector_context: str = "", sentiment_context: str = "",
                suggestion_context: str = "") -> Dict:
        prompt = self.PROMPT.format(
            momentum_opinion=json.dumps(momentum_opinion, ensure_ascii=False, indent=2),
            stability_opinion=json.dumps(stability_opinion, ensure_ascii=False, indent=2),
            diff_context=self._build_diff_context(
                momentum_opinion, stability_opinion, holdings, candidates, suggestion_context),
            rule_context=rule_context or "无规则参考",
            sector_context=sector_context or "无板块参考",
            sentiment_context=sentiment_context or "无舆情参考",
            suggestion_context=suggestion_context or "无调仓建议",
        )
        result = self.call_llm(prompt, temperature=0.2)
        return result if result and "error" not in result else {"error": "裁决失败", "decision": "hold", "final_swaps": []}
