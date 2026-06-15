import json
import logging
from datetime import date
from typing import Dict
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.models.sentiment import SentimentData
from app.services.market_environment_service import MarketEnvironmentService

logger = logging.getLogger(__name__)


class SentimentAnalystAgent(BaseAgent):
    name = "sentiment_analyst"

    PROMPT = """你是专业的市场情绪分析师。请基于以下舆情数据和市场情绪指数，分析当前市场情绪状态。

## 市场情绪指数
{sentiment_index}

## 舆情数据汇总
{sentiment_summary}

## 分析要求
基于以上数据，输出JSON格式的情绪分析报告（不要包含其他文字）：
{{
  "market_sentiment": "bullish/bearish/neutral",
  "sentiment_score": -1.0到1.0,
  "confidence": "high/medium/low",
  "positive_factors": ["政策利好"],
  "negative_factors": ["资金流出"],
  "key_news_impact": [
    {{"title": "新闻标题摘要", "impact": "positive/negative/neutral", "weight": "high/medium/low"}}
  ],
  "sentiment_extreme": false,
  "sentiment_extreme_detail": "如果情绪极端，说明原因；否则为空字符串",
  "summary": "一句话总结市场情绪状态"
}}

sentiment_score的取值范围：-1.0(极度悲观)到1.0(极度乐观)，0为中性。"""

    def analyze(self, target_date: date, db: Session) -> Dict:
        env_svc = MarketEnvironmentService()
        sentiment_index = env_svc.build_market_sentiment_index(target_date, db)
        sentiment_summary = self._get_sentiment_summary(target_date, db)

        prompt = self.PROMPT.format(
            sentiment_index=json.dumps(sentiment_index, ensure_ascii=False, indent=2),
            sentiment_summary=json.dumps(sentiment_summary, ensure_ascii=False, indent=2),
        )

        result = self.call_llm(prompt)
        if result and "error" not in result:
            result["data_date"] = target_date.isoformat()
        return result

    def _get_sentiment_summary(self, target_date: date, db: Session) -> Dict:
        sentiments = db.query(SentimentData).filter(
            SentimentData.data_date == target_date,
            SentimentData.sentiment_label.isnot(None)
        ).all()

        if not sentiments:
            return {"total": 0, "message": "无舆情数据"}

        positive = sum(1 for s in sentiments if s.sentiment_label == "positive")
        negative = sum(1 for s in sentiments if s.sentiment_label == "negative")
        avg_score = sum(s.sentiment_score or 0 for s in sentiments) / len(sentiments)

        key_news = [
            {"title": s.title, "score": s.sentiment_score, "factors": s.key_factors}
            for s in sorted(sentiments, key=lambda x: abs(x.sentiment_score or 0), reverse=True)[:5]
        ]

        return {
            "total": len(sentiments),
            "positive_count": positive,
            "negative_count": negative,
            "avg_sentiment_score": round(avg_score, 2),
            "key_news": key_news,
        }
