"""分析工具 - 多Agent分析、舆情、技术指标、经验匹配"""

import logging
import math
from datetime import date, timedelta
from typing import Dict, List

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
    from app.services.smart_experience_matcher import get_smart_experience_matcher

    matcher = get_smart_experience_matcher()
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


def _ic_stats(values: List[float]) -> Dict:
    """IC 序列统计：均值 / 样本标准差（ddof=1）/ ICIR

    ICIR = mean(IC) / std(IC)：只看均值会把「一天很准、一天反向」的噪声因子当有效因子，
    除以标准差惩罚的正是这种不稳定。观测<2 或标准差为 0 时 ICIR 不可算（返回 None）。
    """
    n = len(values)
    if n == 0:
        return {"obs_days": 0, "ic_mean": None, "ic_std": None, "icir": None}
    mean = sum(values) / n
    if n < 2:
        return {"obs_days": 1, "ic_mean": round(mean, 4), "ic_std": None, "icir": None}
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    return {
        "obs_days": n,
        "ic_mean": round(mean, 4),
        "ic_std": round(std, 4),
        "icir": round(mean / std, 4) if std > 1e-12 else None,
    }


def _ic_reliability(stats: Dict) -> str:
    """IC 稳定性标签：按 |ICIR| 分档；标准差为 0 时 ICIR 无定义但方向完全一致"""
    if stats["obs_days"] < 2:
        return "样本不足"
    icir = stats["icir"]
    if icir is None:
        return "IC 无波动"
    return "稳定" if abs(icir) >= 0.5 else "一般" if abs(icir) >= 0.3 else "噪声"


@tool(name="get_factor_performance", description="获取因子表现画像：当前自适应打分权重 + 各因子近期 IC 均值与 ICIR，用于判断哪个因子仍有效、哪个已失效")
def get_factor_performance(db: Session, ic_days: int = 20, lookback_days: int = 30) -> dict:
    """因子表现画像

    Args:
    ic_days: IC 统计窗口（自然日；每因子需≥2 个截面日才能算 ICIR）
    lookback_days: 自适应权重的回看窗口（自然日）
    """
    from app.services.factor_performance_service import (
        DEFAULT_WEIGHTS, FACTORS, get_factor_performance_service,
    )

    svc = get_factor_performance_service()
    ic_days = max(5, min(int(ic_days), 250))
    lookback_days = max(5, min(int(lookback_days), 250))

    history = svc.get_ic_history(db, days=ic_days)
    samples: Dict[str, List[float]] = {f: [] for f in FACTORS}
    for day in history:
        for fname, ic in (day.get("ic") or {}).items():
            samples.setdefault(fname, []).append(float(ic))

    factors = []
    for fname in FACTORS:
        stats = _ic_stats(samples.get(fname) or [])
        factors.append({"factor": fname, **stats, "reliability": _ic_reliability(stats)})
    # 按 |IC 均值| 降序：与自适应权重口径（|IC| 归一）一致，ICIR 只作稳定性注脚
    factors.sort(key=lambda f: -abs(f["ic_mean"] or 0))

    adaptive = svc.get_adaptive_weights(db, lookback_days=lookback_days)
    weights = dict(adaptive or DEFAULT_WEIGHTS)

    ranked = [f for f in factors if f["icir"] is not None]
    parts = [f"IC 窗口 {ic_days} 自然日内有 {len(history)} 个交易日截面样本"]
    if ranked:
        best = max(ranked, key=lambda f: abs(f["icir"]))
        worst = min(ranked, key=lambda f: abs(f["icir"]))
        parts.append(f"最可靠因子 {best['factor']}（ICIR {best['icir']:+.2f}），"
                     f"最弱 {worst['factor']}（ICIR {worst['icir']:+.2f}）")
    else:
        parts.append("IC 观测不足，ICIR 暂不可用")
    parts.append("权重来源：" + ("自适应（按 |IC| 归一）" if adaptive else "固定默认（IC 数据不足）"))

    return {
        "status": "ok" if adaptive else "insufficient_ic",
        "window": {"ic_days": ic_days, "lookback_days": lookback_days,
                   "ic_observations": len(history)},
        "weight_source": "adaptive" if adaptive else "default",
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "default_weights": dict(DEFAULT_WEIGHTS),
        "weight_delta": {k: round(weights[k] - DEFAULT_WEIGHTS[k], 4)
                         for k in weights if k in DEFAULT_WEIGHTS},
        "factors": factors,
        "text": "；".join(parts),
    }


@tool(name="get_sector_impact_analysis", description="分析指定策略持仓所在的申万一级板块：板块评分/超配低配标记、板块逆风持仓与板块集中度")
def get_sector_impact_analysis(db: Session, strategy_id: int) -> dict:
    """持仓板块影响分析

    Args:
    strategy_id: 策略ID
    """
    from app.models.etf import ETFBasic
    from app.models.strategy import Strategy
    from app.services.risk_controller import RiskController
    from app.services.sector_selection_service import (
        MODE_OFF, SIGNAL_LABELS, format_sector_context,
        get_sector_selection_service,
    )
    from app.services.strategy_service import get_strategy_service

    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return {"error": f"策略 {strategy_id} 不存在"}

    allocation = get_strategy_service().get_effective_target_allocation(strategy)
    codes = [c for c in allocation if c]
    if not codes:
        return {"error": "策略无有效持仓配置，板块影响分析不可用"}

    # 板块归类靠 ETF 名称做关键词反查，一次取回名称（逐只查会随持仓数变成 N+1）
    names = {
        r.etf_code: (r.etf_name or "")
        for r in db.query(ETFBasic.etf_code, ETFBasic.etf_name)
        .filter(ETFBasic.etf_code.in_(codes)).all()
    }
    items = [{"etf_code": c, "etf_name": names.get(c, ""),
              "weight": float(allocation.get(c) or 0)} for c in codes]

    svc = get_sector_selection_service()
    mode = svc.get_mode()
    svc.annotate(db, items)
    holdings, _, meta = svc.split(items, [], mode)

    entries = []
    for h in holdings:
        sig = h.get("sector") or {}
        entries.append({
            "etf_code": h["etf_code"],
            "etf_name": h["etf_name"],
            "weight": round(h["weight"], 4),
            "sector": sig.get("sector"),
            "score": sig.get("score"),
            "signal": sig.get("signal"),
            "signal_label": SIGNAL_LABELS.get(sig.get("signal"), "中性"),
            "rank": sig.get("rank"),
            "total": sig.get("total"),
            "as_of": sig.get("as_of"),
            "headwind": bool(h.get("sector_headwind")),
        })
    matched = [e for e in entries if e["sector"]]
    unmatched = [e["etf_code"] for e in entries if not e["sector"]]

    if not matched:
        return {
            "strategy_id": strategy_id, "mode": mode, "status": "no_sector_data",
            "holdings": entries, "unmatched_codes": unmatched,
            "message": "持仓未匹配到申万板块（sw_sector 阶段未同步或名称无法归类），板块信号按中性处理",
        }

    exposure: Dict[str, Dict] = {}
    for e in matched:
        item = exposure.setdefault(e["sector"], {
            "sector": e["sector"], "weight": 0.0, "count": 0,
            "score": e["score"], "signal": e["signal"], "codes": [],
        })
        item["weight"] += e["weight"]
        item["count"] += 1
        item["codes"].append(e["etf_code"])
    exposure_list = sorted(exposure.values(), key=lambda x: -x["weight"])
    for item in exposure_list:
        item["weight"] = round(item["weight"], 4)

    # 板块集中度阈值与风控预算同源（该阈值此前只在配置里、没有消费方）
    limit = RiskController.RISK_CONFIG["risk_budget"]["max_sector_concentration"]
    concentrated = [x for x in exposure_list if x["weight"] > limit]
    headwind_weight = round(sum(e["weight"] for e in entries if e["headwind"]), 4)

    if concentrated:
        detail = "超限：" + "、".join(f"{x['sector']} {x['weight']:.0%}" for x in concentrated)
    else:
        detail = "合规"

    parts = [f"持仓 {len(entries)} 只，命中板块 {len(matched)} 只（未归类 {len(unmatched)} 只）"]
    parts.append(f"逆风（低配板块）持仓 {len(meta['headwind_holdings'])} 只，合计权重 {headwind_weight:.0%}")
    parts.append(f"板块集中度{detail}（单板块上限 {limit:.0%}）")
    parts.append("板块层未启用（sector_rotation_mode=off），以下仅为展示，不参与选型"
                 if mode == MODE_OFF else
                 "板块口径：申万一级评分 ≥67 超配 / ≤33 低配")

    return {
        "strategy_id": strategy_id,
        "mode": mode,
        "status": "ok",
        "as_of": matched[0]["as_of"],
        "holdings": entries,
        "unmatched_codes": unmatched,
        "headwind_holdings": meta["headwind_holdings"],
        "headwind_weight": headwind_weight,
        "sector_exposure": exposure_list,
        "concentrated_sectors": [x["sector"] for x in concentrated],
        "sector_concentration_limit": limit,
        "context_text": format_sector_context(holdings, [], meta),
        "text": "；".join(parts),
    }
