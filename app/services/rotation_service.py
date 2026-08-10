"""ETF轮动决策服务（量化筛选 + 多Agent辩论裁决）"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.strategy import Strategy
from app.models.etf import ETFDailyIndicator, ETFBasic
from app.services.market_scanner_service import get_market_scanner_service

logger = logging.getLogger(__name__)

MAX_HOLDINGS = 5
MIN_HOLD_DAYS = 5
SCORE_GAP_THRESHOLD = 5.0


class RotationService:
    """轮动决策：量化筛选候选 → 多Agent辩论 → 裁决执行"""

    def evaluate_rotation(self, strategy_id: int, scan_date: date, db: Session) -> Dict:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy or not strategy.allocation_config:
            return {"action": "skip", "reason": "策略不存在或未配置"}

        current_holdings = list(strategy.allocation_config.keys())
        scanner = get_market_scanner_service()

        holding_scores = scanner.get_holding_scores(scan_date, current_holdings, db)
        top_candidates = scanner.get_top_n(scan_date, MAX_HOLDINGS * 3, db)

        enter_candidates = [
            c for c in top_candidates
            if c["etf_code"] not in current_holdings
        ][:MAX_HOLDINGS * 2]

        # 失败模式规避：剔除重复失败的候选标的
        from app.services.failure_mode_service import get_failure_mode_service
        banned = get_failure_mode_service().get_banned_codes(db)
        if banned:
            excluded = [c for c in enter_candidates if c["etf_code"] in banned]
            if excluded:
                logger.info(f"[Rotation] 规避重复失败候选: {[c['etf_code'] for c in excluded]}")
            enter_candidates = [c for c in enter_candidates if c["etf_code"] not in banned]

        if not holding_scores:
            return {"action": "skip", "reason": "持仓ETF无指标数据"}

        if not enter_candidates:
            return {"action": "hold", "reason": "无候选标的"}

        eligible_holdings = [
            h for h in holding_scores
            if self._check_min_hold_period(strategy_id, h["etf_code"], scan_date, db)
        ]

        has_gap = any(
            enter_candidates[0]["composite_score"] - h["composite_score"] >= SCORE_GAP_THRESHOLD
            for h in eligible_holdings
        ) if enter_candidates and eligible_holdings else False

        if not has_gap:
            return {
                "action": "hold",
                "reason": "候选与持仓得分差距不足，无需辩论",
                "holdings": [{
                    "code": h["etf_code"],
                    "name": h.get("etf_name", ""),
                    "score": h["composite_score"],
                    "rank": h.get("rank", 0),
                } for h in sorted(holding_scores, key=lambda x: -x["composite_score"])],
            }

        debate_result = self._run_debate(eligible_holdings, enter_candidates)

        if debate_result.get("decision") != "rotate" or not debate_result.get("final_swaps"):
            return {
                "action": "hold",
                "reason": debate_result.get("summary", "辩论裁决维持持仓"),
                "debate": debate_result,
            }

        rotations = []
        for swap in debate_result["final_swaps"][:2]:
            remove_code = swap.get("remove", "")
            add_code = swap.get("add", "")
            if not remove_code or not add_code:
                continue
            remove_info = next((h for h in holding_scores if h["etf_code"] == remove_code), {})
            add_info = next((c for c in enter_candidates if c["etf_code"] == add_code), {})
            rotations.append({
                "remove": remove_code,
                "remove_name": remove_info.get("etf_name", ""),
                "remove_score": remove_info.get("composite_score", 0),
                "remove_rank": remove_info.get("rank", 0),
                "add": add_code,
                "add_name": add_info.get("etf_name", ""),
                "add_score": add_info.get("composite_score", 0),
                "add_rank": add_info.get("rank", 0),
                "score_gap": round(add_info.get("composite_score", 0) - remove_info.get("composite_score", 0), 2),
                "reason": swap.get("reason", ""),
                "weight_suggestion": swap.get("weight_suggestion"),
            })

        if not rotations:
            return {"action": "hold", "reason": "辩论裁决无有效替换", "debate": debate_result}

        return {
            "action": "rotate",
            "rotations": rotations,
            "holdings_before": current_holdings,
            "scan_date": scan_date.isoformat(),
            "debate": debate_result,
        }

    def execute_rotation(self, strategy_id: int, rotation_plan: Dict, db: Session) -> Dict:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "failed", "reason": "策略不存在"}

        rotations = rotation_plan.get("rotations", [])
        if not rotations:
            return {"status": "skip", "reason": "无轮换计划"}

        old_config = dict(strategy.pending_allocation or strategy.allocation_config)
        new_config = dict(old_config)
        executed = []

        for rot in rotations:
            remove_code = rot["remove"]
            add_code = rot["add"]

            if remove_code not in new_config:
                continue

            weight = new_config.pop(remove_code)
            new_config[add_code] = weight

            executed.append({
                "removed": remove_code,
                "removed_name": rot.get("remove_name", ""),
                "added": add_code,
                "added_name": rot.get("add_name", ""),
                "weight": weight,
            })

        if not executed:
            return {"status": "skip", "reason": "无有效轮换执行"}

        total = sum(new_config.values())
        if abs(total - 1.0) > 0.01:
            factor = 1.0 / total
            new_config = {k: round(v * factor, 4) for k, v in new_config.items()}

        # t+1 生效：写入待生效配置，下一交易日按新配置执行交易
        from app.services.strategy_service import get_strategy_service
        try:
            mode = get_strategy_service().stage_allocation_change(strategy, new_config, db)
            strategy.last_auto_analysis_date = date.today()
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[Rotation] 策略{strategy_id}轮换提交失败: {e}")
            return {"status": "failed", "reason": str(e)}

        logger.info(f"[Rotation] 策略{strategy_id}轮换已提交: {len(executed)}只替换, 生效方式={mode}")
        return {
            "status": "ok",
            "executed": executed,
            "new_allocation": new_config,
            "effective": mode,
        }

    def _run_debate(self, holdings: List[Dict], candidates: List[Dict]) -> Dict:
        from app.agents.rotation_debate.orchestrator import RotationDebateOrchestrator
        from app.config import get_settings

        settings = get_settings()
        if not (settings.llm_api_key and settings.llm_api_key.strip()):
            logger.info("[Rotation] LLM未配置，降级为纯量化裁决")
            return self._fallback_quant_decision(holdings, candidates)

        try:
            debate = RotationDebateOrchestrator()
            return debate.debate(holdings, candidates)
        except Exception as e:
            logger.warning(f"[Rotation] 辩论异常，降级纯量化: {e}")
            return self._fallback_quant_decision(holdings, candidates)

    def _fallback_quant_decision(self, holdings: List[Dict], candidates: List[Dict]) -> Dict:
        sorted_holdings = sorted(holdings, key=lambda x: x["composite_score"])
        sorted_candidates = sorted(candidates, key=lambda x: -x["composite_score"])

        swaps = []
        for weak in sorted_holdings:
            if not sorted_candidates:
                break
            best = sorted_candidates[0]
            gap = best["composite_score"] - weak["composite_score"]
            if gap < SCORE_GAP_THRESHOLD:
                break
            swaps.append({
                "remove": weak["etf_code"],
                "add": best["etf_code"],
                "reason": f"量化得分差距{gap:.1f}分（纯量化降级裁决）",
                "weight_suggestion": None,
            })
            sorted_candidates.pop(0)
            if len(swaps) >= 2:
                break

        if not swaps:
            return {"decision": "hold", "final_swaps": [], "summary": "得分差距不足，维持持仓"}

        return {"decision": "rotate", "final_swaps": swaps, "summary": f"纯量化裁决替换{len(swaps)}只"}

    def _check_min_hold_period(self, strategy_id: int, etf_code: str,
                               scan_date: date, db: Session) -> bool:
        from app.models.portfolio import TradeRecord
        last_buy = db.query(TradeRecord).filter(
            TradeRecord.strategy_id == strategy_id,
            TradeRecord.etf_code == etf_code,
            TradeRecord.direction == "buy",
        ).order_by(TradeRecord.trade_date.desc()).first()

        if not last_buy:
            return True

        hold_days = (scan_date - last_buy.trade_date).days
        return hold_days >= MIN_HOLD_DAYS


_service: RotationService | None = None


def get_rotation_service() -> RotationService:
    global _service
    if _service is None:
        _service = RotationService()
    return _service
