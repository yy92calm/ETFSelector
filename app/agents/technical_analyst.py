import json
import logging
from datetime import date
from typing import Dict, List
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.services.technical_indicator_service import TechnicalIndicatorService

logger = logging.getLogger(__name__)


class TechnicalAnalystAgent(BaseAgent):
    name = "technical_analyst"

    PROMPT = """你是专业的ETF量化技术分析师。请基于以下技术指标数据，给出客观的技术面分析报告。

## 技术指标数据
{technical_data}

## 分析要求
基于以上数据，输出JSON格式的技术分析报告（不要包含其他文字）：
{{
  "overall_trend": "strong_bullish/bullish/neutral/bearish/strong_bearish",
  "trend_confidence": 0.0-1.0,
  "key_indicators": [
    {{"indicator": "MA5突破MA10", "etf": "510050", "signal": "bullish/bearish/neutral", "weight": "high/medium/low"}}
  ],
  "etf_rankings": [
    {{"etf_code": "510050", "score": 0.0-1.0, "trend": "bullish/neutral/bearish", "reason": "..."}}
  ],
  "strength": 0.0-1.0,
  "positive_signals": ["信号描述"],
  "negative_signals": ["信号描述"],
  "summary": "一句话总结技术面状态"
}}

注意：strength字段代表整体技术面强度，0最弱1最强。
etf_rankings是对各ETF技术面的排序评分，用于后续配置决策参考。"""

    def analyze(self, etf_codes: List[str], db: Session, lock_date: Optional[date] = None) -> Dict:
        tech_svc = TechnicalIndicatorService()
        indicators = tech_svc.batch_calculate_indicators(etf_codes, db, end_date=lock_date)

        prompt = self.PROMPT.format(
            technical_data=json.dumps(indicators, ensure_ascii=False, indent=2),
        )

        result = self.call_llm(prompt)
        if result and "error" not in result:
            result["indicators_raw"] = indicators
            result["etf_codes_analyzed"] = list(etf_codes)
        return result
