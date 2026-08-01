import json
import logging
from datetime import date, timedelta
from typing import Dict, List
from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.sentiment import SentimentData

logger = logging.getLogger(__name__)
settings = get_settings()


class PolicyImpactService:
    """政策事件冲击评估服务 - 采集政策文本并评估对ETF板块的影响"""

    POLICY_KEYWORDS = [
        "央行", "证监会", "国常会", "国务院", "发改委", "财政部",
        "降准", "降息", "加息", "MLF", "LPR", "逆回购",
        "监管", "新规", "意见", "通知", "规划", "纲要",
    ]

    IMPACT_PROMPT = """你是政策影响评估分析师。基于以下政策相关新闻，评估对各ETF板块的影响。

## 政策新闻
{policy_news}

## 当前可配置ETF
{available_etfs}

## 分析要求
输出JSON（不要包含其他文字）：
{{
  "policy_events": [
    {{
      "event": "政策事件摘要",
      "source": "发布机构",
      "impact_direction": "positive/negative/neutral",
      "impact_strength": 1-5,
      "affected_sectors": ["受影响板块"],
      "affected_etfs": ["相关ETF代码"],
      "duration": "short_term/medium_term/long_term",
      "confidence": 0.0-1.0
    }}
  ],
  "net_impact": {{
    "overall_direction": "positive/negative/mixed/neutral",
    "strongest_beneficiary": "最大受益方向",
    "strongest_victim": "最大受损方向"
  }},
  "action_suggestion": "基于政策面的配置建议",
  "summary": "一句话总结政策面状态"
}}

impact_strength评分标准：
1=轻微影响，2=一般影响，3=显著影响，4=重大影响，5=历史性变革"""

    def __init__(self):
        self.llm_client = None
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.llm_client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )

    def assess_policy_impact(self, db: Session, available_etfs: List[str] = None,
                             lookback_days: int = 3) -> Dict:
        if not self.llm_client:
            return {"error": "LLM客户端未配置"}

        policy_news = self._filter_policy_news(db, lookback_days)
        if not policy_news:
            return {
                "policy_events": [],
                "net_impact": {"overall_direction": "neutral"},
                "summary": "近期无重大政策事件",
            }

        prompt = self.IMPACT_PROMPT.format(
            policy_news=json.dumps(policy_news[:20], ensure_ascii=False),
            available_etfs=json.dumps(available_etfs or [], ensure_ascii=False),
        )

        try:
            response = self.llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=2000,
            )
            content = response.choices[0].message.content
            return self._parse_json(content)
        except Exception as e:
            logger.error(f"政策影响评估LLM调用失败: {e}")
            return {"error": str(e)}

    def _filter_policy_news(self, db: Session, lookback_days: int) -> List[Dict]:
        cutoff = date.today() - timedelta(days=lookback_days)
        records = db.query(SentimentData).filter(
            SentimentData.data_date >= cutoff
        ).order_by(SentimentData.data_date.desc()).limit(100).all()

        policy_news = []
        for r in records:
            title = r.title or ""
            if any(kw in title for kw in self.POLICY_KEYWORDS):
                policy_news.append({
                    "title": title,
                    "date": r.data_date.isoformat() if r.data_date else "",
                    "sentiment": r.sentiment_label,
                })

        return policy_news

    def _parse_json(self, content: str) -> Dict:
        import re
        try:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning(f"政策影响JSON解析失败: {e}")
        return {"error": "无法解析响应"}


_service: PolicyImpactService | None = None


def get_policy_impact_service() -> PolicyImpactService:
    global _service
    if _service is None:
        _service = PolicyImpactService()
    return _service
