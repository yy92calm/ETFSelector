import json
import logging
from typing import Dict, List
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class StabilityAdvocate(BaseAgent):
    name = "stability_advocate"

    PROMPT = """你是轮动辩论中的稳定派分析师。你的核心信念：**频繁换仓是收益的敌人，交易成本和追涨杀跌会侵蚀利润，除非有充分理由否则应维持持仓**。

## 当前持仓（含量化得分）
{holdings}

## 全市场候选Top（含量化得分）
{candidates}

## 市场宏观环境
{macro_context}

## 你的任务
从稳定性/交易成本/均值回归角度，审视候选替换是否合理。输出JSON（不要包含其他文字）：
{{
  "stance": "conservative_hold",
  "objections": [
    {{
      "against_removing": "反对换出的ETF代码",
      "reason": "反对理由（均值回归/短期噪音/交易成本等）"
    }}
  ],
  "acceptable_swaps": [
    {{
      "remove": "可以接受换出的ETF代码",
      "add": "可以接受换入的ETF代码",
      "condition": "接受条件"
    }}
  ],
  "risk_warnings": ["换仓风险提示"],
  "confidence": 0.0-1.0,
  "summary": "一句话总结稳定派观点"
}}

注意：你不是完全反对换仓，而是要求充分理由。如果某只持仓确实趋势破位（得分远低于候选），你也应该同意替换。"""

    def analyze(self, holdings: List[Dict], candidates: List[Dict], macro_context: str = "") -> Dict:
        prompt = self.PROMPT.format(
            holdings=json.dumps(holdings, ensure_ascii=False, indent=2),
            candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
            macro_context=macro_context or "无额外宏观信息",
        )
        result = self.call_llm(prompt, temperature=0.4)
        return result if result and "error" not in result else {"error": "稳定派分析失败", "stance": "conservative_hold", "acceptable_swaps": []}
