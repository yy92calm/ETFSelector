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
    result = svc.calculate_all_indicators(etf_code, db)
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


@tool(name="trigger_review", description="手动触发指定策略的复盘：评估经验、落规则快照并执行提示词自进化（含LLM调用，耗时1-3分钟）")
def trigger_review(db: Session, strategy_id: int,
                   review_type: str = "weekly") -> dict:
    """手动触发复盘

    Args:
    strategy_id: 待复盘的策略ID
    review_type: 复盘类型，weekly=近7天，monthly=近30天
    """
    from app.models.strategy import Strategy
    from app.services.review_service import ReviewService

    if review_type not in ("weekly", "monthly"):
        return {"error": "review_type 仅支持 weekly 或 monthly"}
    if not db.query(Strategy).filter(Strategy.id == strategy_id).first():
        return {"error": f"策略{strategy_id}不存在"}

    result = ReviewService().trigger_review(strategy_id, review_type, db)
    evolution = result.get("prompt_evolution") or {}
    report = result.get("review_report") or {}
    return {
        "strategy_id": strategy_id,
        "review_type": result["review_type"],
        "experiences_generated": result["experiences_generated"],
        "review_statistics": report.get("statistics"),
        "prompt_version": evolution.get("version"),
        "evolution_summary": evolution.get("evolution_summary"),
    }


def _latest_trade_date(db: Session) -> date:
    """行情表最近交易日（与 auto_strategy 路由行为一致）"""
    from sqlalchemy import func
    from app.models.etf import ETFQuotation
    d = db.query(func.max(ETFQuotation.trade_date)).scalar()
    return d or date.today()


@tool(name="get_review_report", description="获取指定策略的复盘报告（只读，不触发LLM和提示词进化），含期间统计与新经验列表")
def get_review_report(db: Session, strategy_id: int,
                      review_type: str = "weekly") -> dict:
    """获取复盘报告

    Args:
    strategy_id: 策略ID
    review_type: 报告类型，weekly=近7天，monthly=近30天
    """
    from app.services.review_service import ReviewService

    if review_type not in ("weekly", "monthly"):
        return {"error": "review_type 仅支持 weekly 或 monthly"}
    return ReviewService().get_review_report(strategy_id, review_type, db)


@tool(name="get_execution_logs", description="获取策略近N天的AI自动管道执行日志（各阶段状态、失败原因、动作类型），用于排查为什么没调仓或哪一步失败")
def get_execution_logs(db: Session, strategy_id: int, days: int = 7) -> dict:
    """获取执行日志

    Args:
    strategy_id: 策略ID
    days: 回看天数
    """
    from app.models.auto_strategy_log import AutoStrategyLog

    logs = (
        db.query(AutoStrategyLog)
        .filter(
            AutoStrategyLog.strategy_id == strategy_id,
            AutoStrategyLog.log_date >= date.today() - timedelta(days=days),
        )
        .order_by(AutoStrategyLog.log_date.desc())
        .limit(50)
        .all()
    )
    return {
        "strategy_id": strategy_id,
        "period_days": days,
        "total": len(logs),
        "logs": [
            {
                "log_date": l.log_date.isoformat() if l.log_date else None,
                "status": l.status,
                "action_type": l.action_type,
                "detail": (l.analysis_result or {}) if isinstance(l.analysis_result, dict) else str(l.analysis_result)[:500],
            }
            for l in logs
        ],
    }


@tool(name="get_drawdown_attribution", description="回撤归因分析：识别组合回撤的主要来源ETF与原因类型")
def get_drawdown_attribution(db: Session, strategy_id: int) -> dict:
    """回撤归因

    Args:
    strategy_id: 策略ID
    """
    from app.agents.drawdown_attribution_agent import DrawdownAttributionAgent

    return DrawdownAttributionAgent().analyze(strategy_id, db)


@tool(name="find_similar_environments", description="查找与当前市场环境相似的历史时点（情绪指数、阶段、涨跌统计），辅助判断当前位置")
def find_similar_environments(db: Session, strategy_id: int, top_k: int = 5) -> dict:
    """相似历史环境

    Args:
    strategy_id: 策略ID
    top_k: 返回最相似的N个时点
    """
    from app.services.market_environment_service import MarketEnvironmentService

    svc = MarketEnvironmentService()
    return svc.find_similar_market_environments(
        strategy_id, _latest_trade_date(db), db, top_k
    )


@tool(name="smart_match_experiences", description="智能经验匹配：按当前市场环境场景标签检索策略的活跃历史经验")
def smart_match_experiences(db: Session, strategy_id: int) -> dict:
    """智能经验匹配

    Args:
    strategy_id: 策略ID
    """
    from app.services.smart_experience_matcher import SmartExperienceMatcher

    matcher = SmartExperienceMatcher()
    scenario = matcher.get_current_market_scenario(_latest_trade_date(db), db)
    matched = matcher.match_experiences_by_scenario(strategy_id, scenario, db)
    # 转纯字典（含 id，供决策留痕记录「本次参考了哪些经验」）
    items = [{
        "id": m["experience"].id,
        "title": m["experience"].title,
        "experience_type": m["experience"].experience_type,
        "key_insight": m["experience"].key_insight,
        "result": m["experience"].result,
        "scenario_similarity": m.get("scenario_similarity"),
        "adjusted_weight": m.get("adjusted_weight"),
        "tags_matched": m.get("tags_matched"),
    } for m in matched if m.get("experience") is not None]
    return {
        "current_scenario": scenario,
        "matched_experiences": items[:10],
        "total_matched": len(items),
    }
