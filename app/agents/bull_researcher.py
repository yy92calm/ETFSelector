import json
import logging
from typing import Dict
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class BullResearcher(BaseAgent):
    name = "bull_researcher"

    PROMPT = """你是专业的ETF多头研究员。你的任务是**刻意寻找看多理由**，从以下分析报告中挖掘所有可能的正面信号。

## 技术面分析报告
{technical_report}

## 情绪面分析报告
{sentiment_report}

## 分析要求
请站在最乐观的多头立场，输出JSON格式的多头论证报告（不要包含其他文字）：
{{
  "bullish_case": "详细阐述看多理由",
  "key_bullish_signals": [
    {{"signal": "信号描述", "source": "technical/sentiment", "strength": "strong/moderate/weak"}}
  ],
  "target_etfs": ["ETF代码"],
  "conviction_level": "high/medium/low",
  "risk_to_bull_case": ["即使看多也需要注意的风险"],
  "summary": "一句话总结多头观点"
}}"""

    def analyze(self, technical_report: Dict, sentiment_report: Dict) -> Dict:
        prompt = self.PROMPT.format(
            technical_report=json.dumps(technical_report, ensure_ascii=False, indent=2),
            sentiment_report=json.dumps(sentiment_report, ensure_ascii=False, indent=2),
        )
        result = self.call_llm(prompt, temperature=0.4)
        return result if result else {"error": "BullResearcher分析失败"}
