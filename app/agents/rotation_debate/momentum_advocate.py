import json
import logging
from typing import Dict, List
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class MomentumAdvocate(BaseAgent):
    name = "momentum_advocate"

    PROMPT = """你是轮动辩论中的动量派分析师。你的核心信念：**强者恒强，趋势一旦形成会持续，应该果断换入强势标的、淘汰弱势标的**。

## 当前持仓（含量化得分）
{holdings}

## 全市场候选Top（含量化得分）
{candidates}

## 市场宏观环境
{macro_context}

## 你的任务
从动量/趋势角度，论证哪些持仓应该被替换、替换为哪些候选。输出JSON（不要包含其他文字）：
{{
  "stance": "aggressive_rotate",
  "proposed_swaps": [
    {{
      "remove": "应换出的ETF代码",
      "remove_reason": "换出理由（动量衰减/趋势破位等）",
      "add": "应换入的ETF代码",
      "add_reason": "换入理由（动量强劲/趋势确认等）",
      "urgency": "high/medium/low"
    }}
  ],
  "hold_unchanged": ["应维持的ETF代码及理由"],
  "confidence": 0.0-1.0,
  "summary": "一句话总结动量派观点"
}}

注意：最多建议替换2只。如果持仓都足够强，可以建议不换。"""

    def analyze(self, holdings: List[Dict], candidates: List[Dict], macro_context: str = "") -> Dict:
        prompt = self.PROMPT.format(
            holdings=json.dumps(holdings, ensure_ascii=False, indent=2),
            candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
            macro_context=macro_context or "无额外宏观信息",
        )
        result = self.call_llm(prompt, temperature=0.4)
        return result if result and "error" not in result else {"error": "动量派分析失败", "stance": "aggressive_rotate", "proposed_swaps": []}
