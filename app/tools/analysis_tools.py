"""分析工具 - 多Agent分析、舆情、技术指标、经验匹配"""

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.tools.registry import tool

logger = logging.getLogger(__name__)


@tool(name="run_multi_agent_analysis", description="触发多Agent辩论式市场分析（技术分析师+情绪分析师→多空辩论→研究主管裁决），返回市场阶段判断和配置建议")
def run_multi_agent_analysis(db: Session, strategy_id: int) -> dict:
    from app.agents.orchestrator import Orchestrator

    orchestrator = Orchestrator()
    result = orchestrator.analyze(strategy_id, date.today(), db)

    if "error" in result:
        return {"error": result["error"]}

    # 精简返回，避免过长
    return {
        "analysis_date": result.get("analysis_date"),
        "market_regime": result.get("market_regime"),
        "market_sentiment": result.get("market_sentiment"),
        "suggested_action": result.get("suggested_action"),
        "suggested_allocation": result.get("suggested_allocation"),
        "confidence_level": result.get("confidence_level"),
        "action_reason": result.get("action_reason"),
        "risk_alert": result.get("risk_alert"),
        "agreement_level": result.get("agreement_level"),
    }


@tool(name="get_sentiment_data", description="获取最近N天的舆情数据（财经新闻情感分析结果）")
def get_sentiment_data(db: Session, days: int = 3) -> dict:
    from app.models.sentiment import SentimentData

    cutoff = date.today() - timedelta(days=days)
    sentiments = (
        db.query(SentimentData)
        .filter(SentimentData.data_date >= cutoff)
        .order_by(SentimentData.publish_time.desc())
        .limit(50)
        .all()
    )

    data = [
        {
            "title": s.title,
            "sentiment_score": s.sentiment_score,
            "sentiment_label": s.sentiment_label,
            "related_etfs": s.related_etfs,
            "data_date": s.data_date.isoformat() if s.data_date else None,
        }
        for s in sentiments
    ]

    # 统计
    positive = sum(1 for s in sentiments if s.sentiment_label == "positive")
    negative = sum(1 for s in sentiments if s.sentiment_label == "negative")
    neutral = len(sentiments) - positive - negative

    return {
        "total": len(data),
        "period_days": days,
        "summary": {"positive": positive, "negative": negative, "neutral": neutral},
        "news": data[:20],
    }


@tool(name="get_technical_indicators", description="获取指定ETF的技术指标分析（均线、RSI、MACD等趋势判断）")
def get_technical_indicators(db: Session, etf_code: str) -> dict:
    from app.services.technical_indicator_service import TechnicalIndicatorService

    svc = TechnicalIndicatorService()
    result = svc.analyze_etf(etf_code, db)
    return result


@tool(name="get_experience_insights", description="获取指定策略的历史经验洞察（匹配当前市场环境的过往决策经验）")
def get_experience_insights(db: Session, strategy_id: int) -> dict:
    from app.models.experience import Experience

    experiences = (
        db.query(Experience)
        .filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
        )
        .order_by(Experience.weight.desc())
        .limit(10)
        .all()
    )

    data = [
        {
            "id": e.id,
            "title": e.title,
            "experience_type": e.experience_type,
            "key_insight": e.key_insight,
            "weight": e.weight,
            "effectiveness_score": e.effectiveness_score,
            "application_count": e.application_count,
        }
        for e in experiences
    ]

    return {
        "strategy_id": strategy_id,
        "active_experiences": len(data),
        "top_experiences": data,
    }
