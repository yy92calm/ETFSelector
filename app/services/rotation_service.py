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

    def evaluate_rotation(self, strategy_id: int, scan_date: date, db: Session,
                          gap_threshold: Optional[float] = None,
                          ignore_min_hold: bool = False) -> Dict:
        """评估轮动

        gap_threshold: 覆盖换仓门槛（情绪条件触发时加严；手动复核可放宽）
        ignore_min_hold: 忽略最短持有期（仅用于人工"假设不受限"的评估，默认 False）
        """
        threshold = SCORE_GAP_THRESHOLD if gap_threshold is None else gap_threshold
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy or not strategy.allocation_config:
            return {"action": "skip", "reason": "策略不存在或未配置"}

        # 评估基准与执行基准一致：有待生效配置时以它为准（execute_rotation 同样 pending-first）
        from app.services.strategy_service import get_strategy_service
        base_allocation = get_strategy_service().get_effective_target_allocation(strategy)
        current_holdings = list(base_allocation.keys())
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

        # 调仓建议（LLM 只建议）：纳入候选池并作为辩论材料，未决建议本身触发评估
        from app.services.allocation_suggestion_service import get_allocation_suggestion_service
        suggestion_svc = get_allocation_suggestion_service()
        suggestion_svc.expire_stale(db, strategy_id)
        suggestions = suggestion_svc.get_pending(db, strategy_id)
        suggestion_codes = []
        for sg in suggestions:
            for code in (sg.suggested_allocation or {}):
                if code not in current_holdings and code not in suggestion_codes:
                    suggestion_codes.append(code)

        if banned:
            excluded = [c for c in enter_candidates if c["etf_code"] in banned]
            if excluded:
                logger.info(f"[Rotation] 规避重复失败候选: {[c['etf_code'] for c in excluded]}")
            enter_candidates = [c for c in enter_candidates if c["etf_code"] not in banned]
            suggestion_codes = [c for c in suggestion_codes if c not in banned]

        # 建议标的不在动量Top时补进候选（带真实综合分；是否换入仍由辩论与门槛决定）
        missing = [c for c in suggestion_codes
                   if c not in {x["etf_code"] for x in enter_candidates}]
        if missing:
            extra = scanner.get_holding_scores(scan_date, missing, db)
            for item in extra:
                item["from_suggestion"] = True
            if extra:
                logger.info(f"[Rotation] 建议标的补入候选: {[x['etf_code'] for x in extra]}")
                enter_candidates = enter_candidates + extra

        if not holding_scores:
            return {"action": "skip", "reason": "持仓ETF无指标数据"}

        if not enter_candidates:
            return {"action": "hold", "reason": "无候选标的"}

        if ignore_min_hold:
            eligible_holdings = list(holding_scores)
        else:
            eligible_holdings = [
                h for h in holding_scores
                if self._check_min_hold_period(strategy_id, h["etf_code"], scan_date, db)
            ]
        min_hold_blocked = [h["etf_code"] for h in holding_scores if h not in eligible_holdings]

        # 板块层（申万一级，半硬模式）：附加板块信号 + 低配板块分流；失败降级为仅个股动量
        sector_meta = {"mode": "off"}
        sector_context = ""
        try:
            from app.services.sector_selection_service import (
                get_sector_selection_service, format_sector_context,
            )
            sector_svc = get_sector_selection_service()
            mode = sector_svc.get_mode()
            if mode != "off":
                hit = sector_svc.annotate(db, eligible_holdings + enter_candidates)
                eligible_holdings, enter_candidates, sector_meta = sector_svc.split(
                    eligible_holdings, enter_candidates, mode
                )
                sector_context = format_sector_context(eligible_holdings, enter_candidates, sector_meta)
                logger.info(
                    f"[Rotation] 板块层: 模式={mode} 命中{hit}只 "
                    f"逆风持仓={sector_meta['headwind_holdings']} 降级候选={len(sector_meta['downgraded'])}"
                )
        except Exception as e:
            logger.warning(f"[Rotation] 板块层计算失败（降级为仅个股动量）: {e}")

        # 舆情依据：市场情绪 + 涉本策略标的舆情（与板块/规则同级，hold 路径也留痕）
        sentiment_context = self._build_sentiment_context(strategy, db)

        # 调仓建议材料（LLM 建议不具约束力，采纳须通过辩论与门槛）
        suggestion_context = self._build_suggestion_context(suggestions, holding_scores,
                                                           enter_candidates, base_allocation)

        has_gap = any(
            enter_candidates[0]["composite_score"] - h["composite_score"] >= threshold
            for h in eligible_holdings
        ) if enter_candidates and eligible_holdings else False
        # 有未决建议时提级评估：建议本身就是"值得辩论"的信号（仍受全部硬约束与门槛约束）
        if suggestions and eligible_holdings and enter_candidates:
            has_gap = True

        if not has_gap:
            if min_hold_blocked and not eligible_holdings:
                reason = (f"持仓均未满最短持有期（{MIN_HOLD_DAYS}日），本次不换仓"
                          f"（受限持仓 {len(min_hold_blocked)} 只）")
            elif min_hold_blocked:
                reason = (f"可换出的持仓与最强候选得分差距不足，无需辩论"
                          f"（另有 {len(min_hold_blocked)} 只未满最短持有期）")
            else:
                reason = "候选与持仓得分差距不足，无需辩论"
            if suggestions and not has_gap:
                pass  # has_gap 为 False 且无建议时无需结算；有建议必进辩论（见上方提级逻辑）
            return {
                "action": "hold",
                "reason": reason,
                "min_hold_blocked": min_hold_blocked,
                "ignore_min_hold": ignore_min_hold,
                "holdings": [{
                    "code": h["etf_code"],
                    "name": h.get("etf_name", ""),
                    "score": h["composite_score"],
                    "rank": h.get("rank", 0),
                } for h in sorted(holding_scores, key=lambda x: -x["composite_score"])],
                "sector_meta": sector_meta,
                "sector_context": sector_context,
                "sentiment_context": sentiment_context,
                "gap_threshold": threshold,
            }

        # 板块层面参考信号：行业盈利-估值性价比（不参与综合分排名，仅供辩论）
        self._attach_value_signals(eligible_holdings + enter_candidates, db)

        # 规则依据：当前市场状态下的规则建议配置（与规则驱动回测同源，仅供辩论参考）
        rule_signal = self._build_rule_signal(strategy, scan_date, db, base_allocation)

        debate_result = self._run_debate(eligible_holdings, enter_candidates, rule_signal,
                                         sector_context, sentiment_context, suggestion_context)

        if debate_result.get("decision") != "rotate" or not debate_result.get("final_swaps"):
            if suggestions:
                suggestion_svc.settle(db, strategy_id, "rejected",
                                      "辩论驳回：" + str(debate_result.get("summary") or "")[:200])
            return {
                "action": "hold",
                "reason": debate_result.get("summary", "辩论裁决维持持仓"),
                "debate": debate_result,
                "rule_signal": rule_signal,
                "sector_meta": sector_meta,
                "sector_context": sector_context,
                "sentiment_context": sentiment_context,
                "gap_threshold": threshold,
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
            suggestion_svc.settle(db, strategy_id, "rejected",
                                  "辩论裁决无有效替换：" + str(debate_result.get("summary") or "")[:200])
            return {"action": "hold", "reason": "辩论裁决无有效替换", "debate": debate_result,
                    "rule_signal": rule_signal, "sector_meta": sector_meta,
                    "sector_context": sector_context, "gap_threshold": threshold}

        if suggestions:
            suggestion_svc.settle(
                db, strategy_id, "adopted",
                "轮动通道采纳（%s）：%s" % (
                    "；".join("%s→%s" % (r["remove"], r["add"]) for r in rotations),
                    str(debate_result.get("summary") or "")[:150]),
            )
        return {
            "action": "rotate",
            "rotations": rotations,
            "holdings_before": current_holdings,
            "scan_date": scan_date.isoformat(),
            "debate": debate_result,
            "rule_signal": rule_signal,
            "sector_meta": sector_meta,
            "sector_context": sector_context,
            "sentiment_context": sentiment_context,
            "gap_threshold": threshold,
            "ignore_min_hold": ignore_min_hold,
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

    def _attach_value_signals(self, items: List[Dict], db: Session) -> None:
        """为辩论材料附加行业性价比信号（ETF名称→行业反查）。失败或无数据时静默跳过。"""
        try:
            from app.services.value_model_service import get_value_model_service
            names = {i["etf_code"]: i.get("etf_name", "")
                     for i in items if i.get("etf_code") and i.get("etf_name")}
            if not names:
                return
            signals = get_value_model_service().get_etf_industry_signals(db, names)
            attached = 0
            for i in items:
                sig = signals.get(i.get("etf_code"))
                if sig:
                    i["industry_value"] = sig
                    attached += 1
            if attached:
                logger.info(f"[Rotation] 行业性价比信号已注入辩论材料: {attached}只")
        except Exception as e:
            logger.warning(f"[Rotation] 行业性价比信号注入失败（不影响辩论）: {e}")

    def _build_rule_signal(self, strategy, scan_date: date, db: Session,
                           base_allocation: Optional[Dict] = None) -> Optional[Dict]:
        """规则依据：当前市场状态下的规则建议配置。失败或数据不足时静默降级。"""
        try:
            from app.services.rule_engine import get_rule_engine
            signal = get_rule_engine().get_rule_suggestion(
                scan_date, db,
                strategy_id=strategy.id,
                base_allocation=base_allocation or strategy.allocation_config or {},
            )
            if signal:
                logger.info(
                    f"[Rotation] 规则依据已注入: regime={signal.get('regime')} "
                    f"来源={signal.get('rule_source')} 偏离项="
                    f"{sum(1 for d in signal.get('deviation') or [] if abs(d.get('delta', 0)) > 0.01)}"
                )
            return signal
        except Exception as e:
            logger.warning(f"[Rotation] 规则依据注入失败（不影响辩论）: {e}")
            return None

    def _build_suggestion_context(self, suggestions: List, holding_scores: List[Dict],
                                  candidates: List[Dict], base_allocation: Dict) -> str:
        """把未决调仓建议渲染成辩论材料（建议不具约束力，采纳须通过辩论与门槛）"""
        if not suggestions:
            return ""
        score_map = {x["etf_code"]: x for x in list(holding_scores) + list(candidates)}
        lines = []
        for sg in suggestions[:3]:
            alloc = sg.suggested_allocation or {}
            parts = []
            for code, weight in sorted(alloc.items(), key=lambda x: -x[1]):
                item = score_map.get(code) or {}
                cur = base_allocation.get(code)
                tag = "持仓" if cur else ("候选" if code in {c["etf_code"] for c in candidates} else "新标的")
                score_txt = f" 综合分{item['composite_score']:.1f}" if item.get("composite_score") is not None else ""
                parts.append(f"{code}{item.get('etf_name', '')[:8]} {weight * 100:.0f}%（{tag}{score_txt}）")
            lines.append(f"建议#{sg.id}（{sg.source}，{sg.created_at:%m-%d %H:%M}）：" + "、".join(parts))
            if sg.reason:
                lines.append(f"   建议理由：{str(sg.reason)[:200]}")
        lines.append("说明：调仓建议由 AI 提出但不具约束力——采纳须通过本次辩论（分数差距门槛/板块分层/规则/舆情），"
                     "驳回请给出关键理由（会回执给建议方并留痕）。")
        return "\n".join(lines)

    def _build_sentiment_context(self, strategy, db: Session) -> str:
        """舆情依据文本（失败静默降级为空）"""
        try:
            from app.services.strategy_evidence_service import (
                format_sentiment_context, get_strategy_evidence_service,
            )
            evidence = get_strategy_evidence_service().get_sentiment_evidence(strategy, db)
            text = format_sentiment_context(evidence)
            if evidence and evidence.get("total"):
                logger.info(
                    f"[Rotation] 舆情依据已注入: {evidence['total']}条 "
                    f"均分{evidence.get('avg_score')} 涉标的{len(evidence.get('recent') or [])}条"
                )
            return text
        except Exception as e:
            logger.warning(f"[Rotation] 舆情依据注入失败（不影响辩论）: {e}")
            return ""

    def _run_debate(self, holdings: List[Dict], candidates: List[Dict],
                    rule_signal: Optional[Dict] = None,
                    sector_context: str = "",
                    sentiment_context: str = "",
                    suggestion_context: str = "") -> Dict:
        from app.agents.rotation_debate.orchestrator import RotationDebateOrchestrator
        from app.config import get_settings

        settings = get_settings()
        if not (settings.llm_api_key and settings.llm_api_key.strip()):
            logger.info("[Rotation] LLM未配置，降级为纯量化裁决")
            return self._fallback_quant_decision(holdings, candidates)

        try:
            debate = RotationDebateOrchestrator()
            return debate.debate(holdings, candidates, rule_signal=rule_signal,
                                 sector_context=sector_context,
                                 sentiment_context=sentiment_context,
                                 suggestion_context=suggestion_context)
        except Exception as e:
            logger.warning(f"[Rotation] 辩论异常，降级纯量化: {e}")
            return self._fallback_quant_decision(holdings, candidates)

    def _fallback_quant_decision(self, holdings: List[Dict], candidates: List[Dict]) -> Dict:
        from app.services.sector_selection_service import get_sector_selection_service
        sorted_holdings = get_sector_selection_service().ordering_key(holdings)
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
