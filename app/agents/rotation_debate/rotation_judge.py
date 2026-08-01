import json
import logging
from typing import Dict
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class RotationJudge(BaseAgent):
    name = "rotation_judge"

    PROMPT = """你是轮动决策的最终裁决官。你需要综合动量派和稳定派的观点，做出最终轮换决策。

## 动量派意见
{momentum_opinion}

## 稳定派意见
{stability_opinion}

## 当前持仓
{holdings}

## 候选池
{candidates}

## 裁决规则
- 持仓总数必须≤5只
- 有进必有出（替换制）
- 每次最多替换2只
- 候选得分必须显著高于被替换者（差距≥5分）
- 如果两派意见一致，直接执行
- 如果两派分歧，倾向于不换（除非得分差距>10分）
- 宏观环境为衰退时，优先防御性标的

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

如果决定不换，final_swaps为空数组，decision为"hold"。"""

    def analyze(self, momentum_opinion: Dict, stability_opinion: Dict,
                holdings: list, candidates: list) -> Dict:
        prompt = self.PROMPT.format(
            momentum_opinion=json.dumps(momentum_opinion, ensure_ascii=False, indent=2),
            stability_opinion=json.dumps(stability_opinion, ensure_ascii=False, indent=2),
            holdings=json.dumps(holdings, ensure_ascii=False, indent=2),
            candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
        )
        result = self.call_llm(prompt, temperature=0.2)
        return result if result and "error" not in result else {"error": "裁决失败", "decision": "hold", "final_swaps": []}
