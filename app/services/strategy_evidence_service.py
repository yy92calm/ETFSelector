"""策略决策依据聚合服务

把行情（量化评分/换仓差距）、研究（行业性价比）、规则（regime→配置建议）、
舆情（涉本策略标的）四类依据聚合成策略视角的只读视图，供：
- 工作台策略视图「决策依据」面板
- 每日决策留痕（analysis_result.evidence 快照 + 引用来源）
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.etf import ETFBasic, ETFDailyIndicator
from app.models.sentiment import SentimentData
from app.models.strategy import Strategy

logger = logging.getLogger(__name__)

# 自主决策工具 → 依据类别（用于决策留痕「引用了哪些依据」）
TOOL_SOURCE_MAP: Dict[str, set] = {
    "market": {
        "get_market_overview", "get_etf_detail", "get_etf_history", "get_technical_indicators",
        "get_market_position_signal", "get_market_regime", "search_etf", "run_multi_agent_analysis",
        "get_strategy_evidence", "list_strategies", "get_strategy_detail", "get_portfolio_status",
    },
    "research": {"get_industry_ranking"},
    "rules": {"get_rule_suggestion"},
    "sentiment": {"get_sentiment_data"},
}

SOURCE_LABELS = {"market": "行情", "research": "研究", "rules": "规则", "sentiment": "舆情"}

SENTIMENT_LOOKBACK_DAYS = 7


def classify_tool_sources(tool_names: List[str]) -> Dict[str, List[str]]:
    """把工具调用名映射到依据类别，返回 {类别: [工具名]}（仅含有命中的类别）"""
    result: Dict[str, List[str]] = {}
    for domain, names in TOOL_SOURCE_MAP.items():
        hit = sorted({t for t in tool_names if t in names})
        if hit:
            result[domain] = hit
    return result


class StrategyEvidenceService:
    """策略决策依据聚合（只读）"""

    def get_evidence(self, strategy_id: int, db: Session) -> Dict:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"exists": False}

        scan_date = db.query(func.max(ETFDailyIndicator.trade_date)).scalar()
        return {
            "exists": True,
            "strategy_id": strategy_id,
            "strategy_name": strategy.name,
            "as_of": scan_date.isoformat() if scan_date else None,
            "market": self._section(self._market_evidence, strategy, db, scan_date),
            "research": self._section(self._research_evidence, strategy, db),
            "rules": self._section(self._rule_evidence, strategy, db, scan_date),
            "sentiment": self._section(self._sentiment_evidence, strategy, db),
            "decision": self._section(self._last_decision, strategy, db),
        }

    def get_snapshot(self, strategy_id: int, db: Session) -> Dict:
        """决策时点依据快照（写入 decisions 记录用，去掉明细长数组）"""
        ev = self.get_evidence(strategy_id, db)
        if not ev.get("exists"):
            return {}

        market = ev.get("market") or {}
        research = ev.get("research") or {}
        rules = ev.get("rules") or {}
        sentiment = ev.get("sentiment") or {}
        candidates = market.get("candidates") or []

        return {
            "as_of": ev.get("as_of"),
            "market": {
                "as_of": market.get("as_of"),
                "gap": market.get("gap"),
                "threshold": market.get("threshold"),
                "trigger": market.get("trigger"),
                "weakest": market.get("weakest"),
                "top_candidate": candidates[0] if candidates else None,
            },
            "research": {
                "as_of": research.get("as_of"),
                "matched": research.get("matched"),
                "items": [
                    {"etf_code": i["etf_code"], "industry": i.get("industry"),
                     "rank": i.get("rank"), "total": i.get("total")}
                    for i in (research.get("items") or [])[:5]
                ],
            },
            "rules": {
                "regime": rules.get("regime"),
                "regime_label": rules.get("regime_label"),
                "rule_source": rules.get("rule_source"),
                "rule_source_label": rules.get("rule_source_label"),
                "sample_count": rules.get("sample_count"),
                "suggested_allocation": rules.get("suggested_allocation"),
                "note": rules.get("note"),
            },
            "sentiment": {
                "as_of": sentiment.get("as_of"),
                "total": sentiment.get("total"),
                "avg_score": sentiment.get("avg_score"),
            },
        }

    # ---------------- 四类依据 ----------------

    @staticmethod
    def _section(fn, strategy: Strategy, db: Session, *args):
        """单段依据计算，失败降级为 None 不影响其余段落"""
        try:
            return fn(strategy, db, *args)
        except Exception as e:
            logger.warning(f"[Evidence] 策略{strategy.id} {getattr(fn, '__name__', 'section')} 计算失败: {e}")
            return None

    def _universe(self, strategy: Strategy, db: Session) -> Dict:
        from app.services.portfolio_service import get_portfolio_service
        return get_portfolio_service().get_strategy_universe(strategy.id, db)

    def _name_map(self, codes: List[str], db: Session) -> Dict[str, str]:
        if not codes:
            return {}
        rows = db.query(ETFBasic).filter(ETFBasic.etf_code.in_(codes)).all()
        return {r.etf_code: r.etf_name for r in rows}

    def _market_evidence(self, strategy: Strategy, db: Session, scan_date: Optional[date]) -> Dict:
        from app.services.market_scanner_service import get_market_scanner_service
        from app.services.failure_mode_service import get_failure_mode_service
        from app.services.rotation_service import MAX_HOLDINGS, SCORE_GAP_THRESHOLD

        universe = self._universe(strategy, db)
        codes = sorted(universe["pool"] | universe["holding_codes"])
        if not scan_date or not codes:
            return {"as_of": None, "items": [], "candidates": [], "threshold": SCORE_GAP_THRESHOLD}

        scanner = get_market_scanner_service()
        names = self._name_map(codes, db)
        scores = {s["etf_code"]: s for s in scanner.get_holding_scores(scan_date, codes, db)}
        banned = get_failure_mode_service().get_banned_codes(db)

        items = []
        for code in codes:
            s = scores.get(code)
            if not s:
                continue
            items.append({
                "etf_code": code,
                "etf_name": names.get(code, ""),
                "is_holding": code in universe["holding_codes"],
                "composite_score": s["composite_score"],
                "rank": s.get("rank"),
                "momentum_5d": s.get("momentum_5d"),
                "momentum_20d": s.get("momentum_20d"),
                "trend_strength": s.get("trend_strength"),
                "banned_count": banned.get(code, 0),
            })
        items.sort(key=lambda x: -(x["composite_score"] or 0))

        candidates = [
            c for c in scanner.get_top_n(scan_date, MAX_HOLDINGS * 3, db)
            if c["etf_code"] not in codes and c["etf_code"] not in banned
        ][:3]

        holdings_scored = [i for i in items if i["is_holding"]]
        weakest = min(holdings_scored, key=lambda x: x["composite_score"]) if holdings_scored else None
        best = candidates[0] if candidates else None
        gap = round(best["composite_score"] - weakest["composite_score"], 2) if (best and weakest) else None

        return {
            "as_of": scan_date.isoformat() if hasattr(scan_date, "isoformat") else str(scan_date),
            "items": items,
            "candidates": candidates,
            "weakest": weakest,
            "gap": gap,
            "threshold": SCORE_GAP_THRESHOLD,
            "trigger": gap is not None and gap >= SCORE_GAP_THRESHOLD,
            "banned": {c: n for c, n in banned.items() if c in codes},
        }

    def _research_evidence(self, strategy: Strategy, db: Session) -> Dict:
        from app.services.value_model_service import get_value_model_service

        universe = self._universe(strategy, db)
        codes = sorted(universe["pool"] | universe["holding_codes"])
        if not codes:
            return {"items": [], "matched": 0, "as_of": None}

        names = self._name_map(codes, db)
        signals = get_value_model_service().get_etf_industry_signals(
            db, {c: names.get(c) for c in codes}
        )
        items = []
        for code in codes:
            sig = signals.get(code) or {}
            items.append({
                "etf_code": code,
                "etf_name": names.get(code, ""),
                "is_holding": code in universe["holding_codes"],
                "industry": sig.get("industry"),
                "score": sig.get("score"),
                "rank": sig.get("rank"),
                "total": sig.get("total"),
                "as_of": sig.get("as_of"),
            })
        items.sort(key=lambda x: (x["rank"] is None, x["rank"] if x["rank"] is not None else 9999))
        as_of = max((i["as_of"] for i in items if i.get("as_of")), default=None)
        return {"items": items, "matched": sum(1 for i in items if i.get("industry")), "as_of": as_of}

    def _rule_evidence(self, strategy: Strategy, db: Session, scan_date: Optional[date]) -> Optional[Dict]:
        from app.services.rule_engine import get_rule_engine

        if not scan_date:
            return None
        return get_rule_engine().get_rule_suggestion(
            scan_date, db,
            strategy_id=strategy.id,
            base_allocation=strategy.allocation_config or {},
        )

    def _sentiment_evidence(self, strategy: Strategy, db: Session) -> Dict:
        universe = self._universe(strategy, db)
        codes = universe["pool"] | universe["holding_codes"]
        latest_date = db.query(func.max(SentimentData.data_date)).scalar()
        if not codes or not latest_date:
            return {"as_of": None, "total": 0, "avg_score": None, "recent": []}

        cutoff = latest_date - timedelta(days=SENTIMENT_LOOKBACK_DAYS)
        rows = (
            db.query(SentimentData)
            .filter(SentimentData.data_date >= cutoff)
            .order_by(SentimentData.data_date.desc())
            .all()
        )
        related = [r for r in rows if any(c in codes for c in (r.related_etfs or []))]
        scores = [r.sentiment_score for r in related if r.sentiment_score is not None]
        return {
            "as_of": latest_date.isoformat(),
            "window_days": SENTIMENT_LOOKBACK_DAYS,
            "total": len(related),
            "avg_score": round(sum(scores) / len(scores), 3) if scores else None,
            "recent": [{
                "date": r.data_date.isoformat(),
                "title": r.title,
                "label": r.sentiment_label,
                "etf_codes": [c for c in (r.related_etfs or []) if c in codes],
            } for r in related[:3]],
        }

    def _last_decision(self, strategy: Strategy, db: Session) -> Optional[Dict]:
        import json
        from app.models.auto_strategy_log import AutoStrategyLog

        log = (
            db.query(AutoStrategyLog)
            .filter(AutoStrategyLog.strategy_id == strategy.id,
                    AutoStrategyLog.action_type == "analyzed")
            .order_by(AutoStrategyLog.log_date.desc())
            .first()
        )
        if not log:
            return None
        ar = log.analysis_result or {}
        if isinstance(ar, str):
            try:
                ar = json.loads(ar)
            except Exception:
                ar = {}
        if not isinstance(ar, dict):
            ar = {}
        evidence = ar.get("evidence") or {}
        return {
            "log_date": log.log_date.isoformat() if log.log_date else None,
            "action": ar.get("suggested_action"),
            "reason": ar.get("action_reason"),
            "market_regime": ar.get("market_regime"),
            "key_signals": ar.get("key_signals_summary") or [],
            "risk_level": (ar.get("risk_alert") or {}).get("level"),
            "sources_cited": list(evidence.get("sources_cited") or []),
            "snapshot": evidence.get("snapshot"),
        }


_service: Optional[StrategyEvidenceService] = None


def get_strategy_evidence_service() -> StrategyEvidenceService:
    global _service
    if _service is None:
        _service = StrategyEvidenceService()
    return _service