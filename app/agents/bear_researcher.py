import json
import logging
from typing import Dict
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class BearResearcher(BaseAgent):
    name = "bear_researcher"

    PROMPT = """你是专业的ETF空头研究员。你的任务是**刻意寻找看空理由**，从以下分析报告中挖掘所有可能的负面信号和风险。

## 技术面分析报告
{technical_report}

## 情绪面分析报告
{sentiment_report}

## 分析要求
请站在最悲观的空头立场，输出JSON格式的空头论证报告（不要包含其他文字）：
{{
  "bearish_case": "详细阐述看空理由",
  "key_bearish_signals": [
    {{"signal": "信号描述", "source": "technical/sentiment", "strength": "strong/moderate/weak"}}
  ],
  "etfs_to_reduce": ["建议减仓的ETF代码"],
  "conviction_level": "high/medium/low",
  "risk_to_bear_case": ["即使看空也需要注意的积极因素"],
  "summary": "一句话总结空头观点"
}}"""

    def analyze(self, technical_report: Dict, sentiment_report: Dict) -> Dict:
        prompt = self.PROMPT.format(
            technical_report=json.dumps(technical_report, ensure_ascii=False, indent=2),
            sentiment_report=json.dumps(sentiment_report, ensure_ascii=False, indent=2),
        )
        result = self.call_llm(prompt, temperature=0.4)
        return result if result else {"error": "BearResearcher分析失败"}
