import json
import logging
from typing import Dict, List
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.models.sentiment import SentimentData

logger = logging.getLogger(__name__)


class ThemeDiscoveryAgent(BaseAgent):
    name = "theme_discovery"

    PROMPT = """你是ETF主题/赛道发现分析师。基于近期财经新闻，识别尚未被价格充分反映的新兴投资主题。

## 近期新闻标题
{news_titles}

## 当前已配置ETF
{current_etfs}

## 分析要求
输出JSON（不要包含其他文字）：
{{
  "emerging_themes": [
    {{
      "theme": "主题名称",
      "heat_score": 0.0-1.0,
      "news_frequency": 出现次数,
      "price_reflected": true/false,
      "suggested_etf_keywords": ["搜索ETF的关键词"],
      "urgency": "high/medium/low",
      "reasoning": "为什么认为该主题尚未被充分定价"
    }}
  ],
  "fading_themes": ["正在退潮的主题"],
  "summary": "一句话总结当前主题轮动方向"
}}

判断标准：
- heat_score: 新闻出现频率×情绪强度
- price_reflected: 如果相关ETF近5日涨幅已超5%，视为已反映
- urgency: high=政策驱动/突发事件，medium=趋势性变化，low=长期主题"""

    def analyze(self, current_etfs: List[str], db: Session, days: int = 7) -> Dict:
        news_titles = self._collect_recent_news(db, days)
        if not news_titles:
            return {"error": "近期无舆情数据，无法发现主题"}

        prompt = self.PROMPT.format(
            news_titles=json.dumps(news_titles[:50], ensure_ascii=False),
            current_etfs=json.dumps(current_etfs, ensure_ascii=False),
        )
        result = self.call_llm(prompt)
        if result and "error" not in result:
            result["news_count"] = len(news_titles)
            result["lookback_days"] = days
        return result

    def _collect_recent_news(self, db: Session, days: int) -> List[str]:
        from datetime import date, timedelta
        cutoff = date.today() - timedelta(days=days)
        records = db.query(SentimentData).filter(
            SentimentData.data_date >= cutoff
        ).order_by(SentimentData.data_date.desc()).limit(100).all()

        return [r.title for r in records if r.title]
