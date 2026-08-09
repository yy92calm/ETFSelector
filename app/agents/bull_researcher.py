import json
import logging
from typing import Dict, Optional
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class BullResearcher(BaseAgent):
    name = "bull_researcher"

    PROMPT = """你是专业的ETF多头研究员。你的任务是**刻意寻找看多理由**，从以下分析报告中挖掘所有可能的正面信号。

## 技术面分析报告
{technical_report}

## 情绪面分析报告
{sentiment_report}

## 宏观周期分析
{macro_report}

## 跨资产相关性分析
{cross_asset_report}

## 波动率体制分析
{volatility_report}

## 数据截止日期
{data_date}

## 分析要求
请站在最乐观的多头立场，输出JSON格式的多头论证报告（不要包含其他文字）。如需补充数据，可调用行情工具（get_etf_history/get_etf_detail等）获取近期走势，但注意数据截止日期：
{{
  "bullish_case": "详细阐述看多理由",
  "key_bullish_signals": [
    {{"signal": "信号描述", "source": "technical/sentiment/macro/cross_asset/volatility", "strength": "strong/moderate/weak"}}
  ],
  "target_etfs": ["ETF代码"],
  "conviction_level": "high/medium/low",
  "risk_to_bull_case": ["即使看多也需要注意的风险"],
  "summary": "一句话总结多头观点"
}}"""

    def analyze(self, technical_report: Dict, sentiment_report: Dict,
                macro_report: Dict = None, cross_asset_report: Dict = None,
                volatility_report: Dict = None, data_date: str = "",
                db=None) -> Dict:
        prompt = self.PROMPT.format(
            technical_report=json.dumps(technical_report, ensure_ascii=False, indent=2),
            sentiment_report=json.dumps(sentiment_report, ensure_ascii=False, indent=2),
            macro_report=json.dumps(macro_report or {}, ensure_ascii=False, indent=2) if macro_report else "暂无",
            cross_asset_report=json.dumps(cross_asset_report or {}, ensure_ascii=False, indent=2) if cross_asset_report else "暂无",
            volatility_report=json.dumps(volatility_report or {}, ensure_ascii=False, indent=2) if volatility_report else "暂无",
            data_date=data_date or "未知（以工具返回为准）",
        )
        result = self.call_llm_with_tools(prompt, db=db, temperature=0.4)
        return result if result else {"error": "BullResearcher分析失败"}
