"""风险控制服务"""

import logging
import math
from datetime import date, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.strategy import Strategy
from app.models.portfolio import PortfolioSnapshot
from app.models.auto_strategy_log import AutoStrategyLog
from app.models.etf import ETFBasic

logger = logging.getLogger(__name__)


class RiskController:
    """风险控制器 - 熔断、回撤保护、风险预算、压力测试"""
    
    RISK_CONFIG = {
        "circuit_breaker": {
            "consecutive_loss_threshold": 9999,
            "single_day_loss_threshold": -0.03,
            "total_loss_threshold": -0.10,
            "cooldown_days": 3,
        },
        "drawdown_protection": {
            "warning_level": -0.05,
            "action_level": -0.08,
            "critical_level": -0.12,
            "reduce_ratio_at_critical": 0.5,
        },
        "risk_budget": {
            "max_single_position": 0.30,
            "max_sector_concentration": 0.40,
            "volatility_budget": 0.15,
        },
    }
    
    def check_circuit_breaker(self, strategy_id: int, db: Session,
                              what_if_swaps: Optional[List[Dict]] = None) -> Dict:
        """检查熔断条件；what_if_swaps 给定时附带「若执行这些换仓后」的风险对照（只算不改库）"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "error", "message": "策略不存在"}

        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.strategy_id == strategy_id
        ).order_by(PortfolioSnapshot.trade_date.desc()).limit(10).all()
        result = self._eval_circuit_breaker(strategy, snapshots)
        if what_if_swaps:
            result = {**result, "what_if": self._eval_what_if(strategy, result, what_if_swaps, db)}
        return result

    @staticmethod
    def _apply_swaps(base: Dict[str, float], swaps: List[Dict]) -> tuple:
        """把替换对套到基准配置上，返回 (结果配置, 结构问题说明)

        只做「换出/换入/权重」的换算；能否真的换仓仍由轮动通道裁决（有进必出、
        每次≤2只、最短持有期、禁入名单等硬约束不在风控侧重复实现）。
        """
        target = dict(base)
        problems: List[str] = []
        for item in swaps[:5]:
            if not isinstance(item, dict):
                problems.append(f"{item} 不是 {{'remove': '换出代码', 'add': '换入代码'}} 形式")
                continue
            remove_code = str(item.get("remove") or "").strip()
            add_code = str(item.get("add") or "").strip()
            if not remove_code or not add_code or remove_code == add_code:
                problems.append(f"{remove_code or '?'}→{add_code or '?'} 参数不完整")
                continue
            if remove_code not in target:
                problems.append(f"{remove_code} 不在当前组合")
                continue
            if add_code in target:
                problems.append(f"{add_code} 已在组合")
                continue
            freed = target.pop(remove_code)
            weight = item.get("weight")
            try:
                weight = float(weight) if weight is not None else freed
            except (TypeError, ValueError):
                weight = freed
            target[add_code] = round(weight, 4)
        total = sum(target.values())
        if target and abs(total - 1.0) > 0.01:
            problems.append(f"替换后权重合计 {total:.2f}（应为 1.0）")
        return target, problems

    def _eval_what_if(self, strategy: Strategy, breaker: Dict,
                      swaps: List[Dict], db: Session) -> Dict:
        """假设换仓后的仓位合规与尾部风险对照（用于「要不要换」之前的风险追问）"""
        base = self._effective_allocation(strategy)
        target, problems = self._apply_swaps(base, swaps)
        if not target:
            return {"blocked": True, "structure_problems": problems or ["替换后组合为空"],
                    "message": "假设组合不成立，无法评估"}

        budget = self._eval_risk_budget(target)
        base_value = self._portfolio_value(strategy.id, db)
        # 两套配置放在同一交易日网格上算 VaR，差值才可比（各取自身公共日会串期）
        codes = list(dict.fromkeys([*base, *target]))
        dates, returns = self._grid_for(codes, db)
        var_before = self._var_on_grid(base, dates, returns, base_value=base_value)
        var_after = self._var_on_grid(target, dates, returns, base_value=base_value)
        # 熔断已触发时，任何新换仓都要先解熔断——与"是否值得换"无关
        blocked = (breaker.get("status") == "triggered"
                   or budget.get("status") == "violation"
                   or bool(problems))
        delta = None
        if var_before.get("status") == "ok" and var_after.get("status") == "ok":
            delta = round(var_after["var_pct"] - var_before["var_pct"], 4)
        direction = ("数据不足" if delta is None else
                     "尾部风险上升" if delta > 0.001 else
                     "尾部风险下降" if delta < -0.001 else "尾部风险基本持平")
        return {
            "blocked": blocked,
            "resulting_allocation": target,
            "risk_budget": budget,
            "structure_problems": problems,
            "var_before": var_before.get("var_pct") if var_before.get("status") == "ok" else None,
            "var_after": var_after.get("var_pct") if var_after.get("status") == "ok" else None,
            "var_delta": delta,
            "current_breaker_status": breaker.get("status"),
            "message": (
                ("现网熔断已触发，须先按冷却期处理再谈换仓；"
                 if breaker.get("status") == "triggered" else "")
                + ("；".join(problems) + "；" if problems else "")
                + (f"仓位{'越线' if budget.get('status') == 'violation' else '合规'}，"
                   f"{direction}"
                   + (f"（1日VaR {var_before['var_pct']:.2%}→{var_after['var_pct']:.2%}）"
                      if delta is not None else "")
                   )
            ),
        }

    def assess_risk_map(self, strategies: List[Strategy], db: Session) -> Dict[int, Dict]:
        """批量风控：一次取回这些策略的净值快照（逐策略查会随策略数线性增长）

        返回 {strategy_id: {"circuit_breaker": {...}, "drawdown_protection": {...}}}，
        与 check_circuit_breaker / apply_drawdown_protection 同口径（只读，不写库）。
        """
        ids = [s.id for s in strategies]
        if not ids:
            return {}
        rows = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.strategy_id.in_(ids))
            .order_by(PortfolioSnapshot.strategy_id, PortfolioSnapshot.trade_date.desc())
            .all()
        )
        snaps_by_sid: Dict[int, List[PortfolioSnapshot]] = {}
        for r in rows:
            snaps_by_sid.setdefault(r.strategy_id, []).append(r)
        return {
            s.id: {
                # 与单策略口径一致：熔断看最近10个快照，回撤保护看最近60个
                "circuit_breaker": self._eval_circuit_breaker(s, snaps_by_sid.get(s.id, [])[:10]),
                "drawdown_protection": self._eval_drawdown_protection(s, snaps_by_sid.get(s.id, [])[:60]),
            }
            for s in strategies
        }

    @staticmethod
    def _eval_circuit_breaker(strategy: Strategy, snapshots: List[PortfolioSnapshot]) -> Dict:
        config = RiskController.RISK_CONFIG["circuit_breaker"]
        
        if len(snapshots) < 2:
            return {"status": "normal", "message": "数据不足，无需熔断"}
        
        consecutive_losses = 0
        for i in range(min(len(snapshots)-1, 5)):
            if snapshots[i].profit_pct and snapshots[i].profit_pct < 0:
                consecutive_losses += 1
            else:
                break
        
        if consecutive_losses >= config["consecutive_loss_threshold"]:
            return {
                "status": "triggered",
                "type": "consecutive_loss",
                "reason": f"连续{consecutive_losses}次亏损",
                "action": "pause_strategy",
                "cooldown_days": config["cooldown_days"],
            }
        
        # 单日亏损：与前一交易日资产比较（profit_pct 是累计收益，不能用于单日判断）
        latest_snapshot = snapshots[0]
        if len(snapshots) >= 2 and snapshots[1].total_asset and latest_snapshot.total_asset:
            day_return = (latest_snapshot.total_asset - snapshots[1].total_asset) / snapshots[1].total_asset
            if day_return < config["single_day_loss_threshold"]:
                return {
                    "status": "triggered",
                    "type": "single_day_loss",
                    "reason": f"单日亏损{day_return:.2%}（{snapshots[1].trade_date}→{latest_snapshot.trade_date}）",
                    "action": "pause_strategy",
                    "cooldown_days": config["cooldown_days"],
                }

        if latest_snapshot.total_asset and strategy.initial_capital:
            total_return = (latest_snapshot.total_asset - strategy.initial_capital) / strategy.initial_capital
            if total_return < config["total_loss_threshold"]:
                return {
                    "status": "triggered",
                    "type": "total_loss",
                    "reason": f"累计亏损{total_return:.2%}",
                    "action": "emergency_stop",
                    "cooldown_days": 7,
                }
        
        return {"status": "normal", "message": "未触发熔断条件"}
    
    def resume_if_cooldown_expired(self, db: Session) -> List[Dict]:
        """冷却到期自动恢复（仅风控熔断暂停的策略；人工/LLM 暂停因无冷却天数字段不自动恢复）"""
        today = date.today()
        rows = db.query(Strategy).filter(
            Strategy.auto_strategy_status == "paused",
            Strategy.paused_cooldown_days.isnot(None),
        ).all()

        resumed: List[Dict] = []
        for strategy in rows:
            if not strategy.paused_date:
                continue
            elapsed = (today - strategy.paused_date).days
            if elapsed < strategy.paused_cooldown_days:
                continue
            resumed.append({
                "strategy_id": strategy.id,
                "name": strategy.name,
                "paused_date": strategy.paused_date.isoformat(),
                "cooldown_days": strategy.paused_cooldown_days,
                "reason": strategy.paused_reason,
            })
            strategy.auto_strategy_status = "running"
            strategy.paused_reason = None
            strategy.paused_date = None
            strategy.paused_cooldown_days = None

        if resumed:
            db.commit()
            for item in resumed:
                logger.info(
                    f"[风控] 策略{item['strategy_id']} 冷却期满（{item['paused_date']} + "
                    f"{item['cooldown_days']}天）已自动恢复运行，原暂停原因: {item['reason']}"
                )
        return resumed

    def apply_drawdown_protection(self, strategy_id: int, db: Session) -> Dict:
        """应用回撤保护"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "error", "message": "策略不存在"}

        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.strategy_id == strategy_id
        ).order_by(PortfolioSnapshot.trade_date.desc()).limit(60).all()
        return self._eval_drawdown_protection(strategy, snapshots)

    @staticmethod
    def _eval_drawdown_protection(strategy: Strategy, snapshots: List[PortfolioSnapshot]) -> Dict:
        config = RiskController.RISK_CONFIG["drawdown_protection"]
        
        if len(snapshots) < 5:
            return {"status": "normal", "message": "数据不足"}
        
        peak_value = max(s.total_asset for s in snapshots if s.total_asset)
        current_value = snapshots[0].total_asset
        
        drawdown = (peak_value - current_value) / peak_value if peak_value > 0 else 0
        
        if drawdown > abs(config["critical_level"]):
            allocation = strategy.allocation_config or {}
            reduce_ratio = config["reduce_ratio_at_critical"]
            reduced_allocation = {}

            for code, ratio in allocation.items():
                reduced_allocation[code] = ratio * reduce_ratio

            # 归一化到 1.0，保持 ETF 结构合法（总和必须为1）
            total = sum(reduced_allocation.values())
            if total > 0:
                reduced_allocation = {c: round(r / total, 4) for c, r in reduced_allocation.items()}

            return {
                "status": "critical",
                "drawdown_pct": round(drawdown * 100, 2),
                "peak_value": round(peak_value, 2),
                "current_value": round(current_value, 2),
                "action": "reduce_position",
                "suggested_allocation": reduced_allocation,
                "cash_ratio": round(1 - reduce_ratio, 2),
                "message": f"回撤达到临界水平{drawdown:.2%}，建议权益仓位降至{reduce_ratio:.0%}，剩余转现金防御",
            }
        
        elif drawdown > abs(config["action_level"]):
            return {
                "status": "warning",
                "drawdown_pct": round(drawdown * 100, 2),
                "action": "monitor_closely",
                "message": f"回撤接近警戒水平{drawdown:.2%}，密切关注",
            }
        
        elif drawdown > abs(config["warning_level"]):
            return {
                "status": "alert",
                "drawdown_pct": round(drawdown * 100, 2),
                "action": "prepare_defense",
                "message": f"回撤达到预警水平{drawdown:.2%}，准备防御措施",
            }
        
        return {
            "status": "normal",
            "drawdown_pct": round(drawdown * 100, 2),
            "message": "回撤在安全范围内",
        }
    
    def check_risk_budget(self, strategy_id: int, db: Session) -> Dict:
        """检查风险预算"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "error", "message": "策略不存在"}
        return self._eval_risk_budget(strategy.allocation_config or {})

    @staticmethod
    def _eval_risk_budget(allocation: Dict[str, float]) -> Dict:
        """风险预算判定（传入假设配置即可做 what-if，不改库）"""
        config = RiskController.RISK_CONFIG["risk_budget"]

        violations = []

        max_position = max(allocation.values()) if allocation else 0
        if max_position > config["max_single_position"]:
            violations.append({
                "type": "single_position_exceeded",
                "over_codes": [c for c, w in allocation.items()
                               if w > config["max_single_position"]],
                "current": round(max_position, 2),
                "limit": config["max_single_position"],
                "message": f"单一持仓{max_position:.2%}超过限制{config['max_single_position']:.2%}",
            })

        if violations:
            return {
                "status": "violation",
                "violations": violations,
                "suggested_action": "rebalance_to_limit",
            }

        return {
            "status": "compliant",
            "max_single_position": round(max_position, 2),
            "message": "风险预算合规",
        }

    # ========== 组合风险度量：相关性分散度 / 历史模拟 VaR ==========

    PRICE_WINDOW = 60        # 默认回看窗口（交易日）
    MIN_OBS = 20             # 少于这么多观测时，分位数与相关系数没有统计意义

    @staticmethod
    def _effective_allocation(strategy: Strategy) -> Dict[str, float]:
        """风险度量的基准与轮动/执行口径一致：有待生效配置时以待生效为准"""
        from app.services.strategy_service import get_strategy_service
        return get_strategy_service().get_effective_target_allocation(strategy)

    def _load_price_series(self, codes: List[str], db: Session,
                           window: int = PRICE_WINDOW) -> Dict[str, Dict[date, float]]:
        """一次查回这些标的的收盘价 {code: {trade_date: close}}（逐标的查会随持仓数变成 N+1）"""
        from app.models.etf import ETFQuotation
        if not codes:
            return {}
        # 多留日历余量覆盖停牌与节假日，再按标的截到最近 window+1 个观测
        start = date.today() - timedelta(days=int(window * 1.8) + 20)
        rows = (
            db.query(ETFQuotation.etf_code, ETFQuotation.trade_date, ETFQuotation.close_price)
            .filter(ETFQuotation.etf_code.in_(codes), ETFQuotation.trade_date >= start)
            .order_by(ETFQuotation.etf_code, ETFQuotation.trade_date)
            .all()
        )
        series: Dict[str, Dict[date, float]] = {}
        for code, trade_date, close in rows:
            if close:
                series.setdefault(code, {})[trade_date] = float(close)
        return {code: dict(sorted(days.items())[-(window + 1):]) for code, days in series.items()}

    @staticmethod
    def _aligned_returns(series: Dict[str, Dict[date, float]], codes: List[str]
                         ) -> tuple:
        """在「所有标的都有价」的公共交易日上算日收益，返回 (收益对应日, {code: [日收益]})"""
        common: Optional[set] = None
        for code in codes:
            days = set(series.get(code, {}))
            common = days if common is None else common & days
        dates = sorted(common or [])
        if len(dates) < 2:
            return [], {}
        returns: Dict[str, List[float]] = {}
        for code in codes:
            prices = series[code]
            returns[code] = [prices[dates[i]] / prices[dates[i - 1]] - 1.0
                             if prices[dates[i - 1]] else 0.0
                             for i in range(1, len(dates))]
        return dates[1:], returns

    @staticmethod
    def _pearson(a: List[float], b: List[float]) -> Optional[float]:
        n = len(a)
        if n < 2:
            return None
        ma, mb = sum(a) / n, sum(b) / n
        da = math.sqrt(sum((x - ma) ** 2 for x in a))
        db = math.sqrt(sum((y - mb) ** 2 for y in b))
        if not da or not db:
            return None
        return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (da * db)

    def get_diversification(self, strategy_id: int, db: Session,
                            window: int = PRICE_WINDOW) -> Dict:
        """持仓标的日收益相关系数矩阵与平均相关度（判断组合是否「假分散」）"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "error", "message": "策略不存在"}
        allocation = self._effective_allocation(strategy)
        codes = [c for c in allocation if c]
        if len(codes) < 2:
            return {"status": "insufficient", "codes": codes,
                    "message": "持仓标的不足2只，无相关性可算"}

        series = self._load_price_series(codes, db, window)
        missing = [c for c in codes if c not in series]
        usable = [c for c in codes if c in series]
        dates, returns = self._aligned_returns(series, usable)
        if len(usable) < 2 or len(dates) < self.MIN_OBS:
            return {"status": "insufficient_data", "obs_days": len(dates), "codes": usable,
                    "missing_codes": missing,
                    "message": f"公共交易日仅{len(dates)}天（需≥{self.MIN_OBS}），相关度暂不可用"}

        matrix = {
            a: {b: round(self._pearson(returns[a], returns[b]) or 0.0, 3) for b in usable}
            for a in usable
        }
        pairs = [(a, b) for i, a in enumerate(usable) for b in usable[i + 1:]]
        pair_corr = {p: (matrix[p[0]][p[1]]) for p in pairs}
        avg = sum(pair_corr.values()) / len(pair_corr)
        hi = max(pair_corr, key=pair_corr.get)
        lo = min(pair_corr, key=pair_corr.get)
        level = ("高度同涨同跌（分散不足）" if avg > 0.8
                 else "分散度一般" if avg > 0.5 else "分散良好")
        return {
            "status": "ok",
            "strategy_id": strategy_id,
            "obs_days": len(dates),
            "window_end": dates[-1].isoformat(),
            "codes": usable,
            "missing_codes": missing,
            "matrix": matrix,
            "avg_correlation": round(avg, 3),
            "highest_pair": {"a": hi[0], "b": hi[1], "corr": round(pair_corr[hi], 3)},
            "lowest_pair": {"a": lo[0], "b": lo[1], "corr": round(pair_corr[lo], 3)},
            "text": (f"近{len(dates)}个交易日持仓两两相关度均值{avg:.2f}，{level}；"
                     f"最高={hi[0]}/{hi[1]} {pair_corr[hi]:.2f}，"
                     f"最低={lo[0]}/{lo[1]} {pair_corr[lo]:.2f}"),
        }

    def _grid_for(self, codes: List[str], db: Session,
                  window: int = PRICE_WINDOW) -> tuple:
        """一次查价，取「所有标的都有价」的公共交易日与日收益 (dates, {code: [ret]})"""
        series = self._load_price_series(codes, db, window)
        usable = [c for c in codes if c in series]
        return self._aligned_returns(series, usable)

    def _var_on_grid(self, allocation: Dict[str, float], dates: List[date],
                     returns: Dict[str, List[float]], horizon: float = 1,
                     confidence: float = 0.95, base_value: float = 0) -> Dict:
        """历史模拟法 VaR：按权重合成组合日收益序列后取分位损失（不做正态假设）

        单独收 (dates, returns) 是为了 what-if 能把「换仓前/后」放在**同一交易日网格**上比，
        否则两边各自取公共日，差值就没有可比性。
        """
        codes = [c for c in allocation if c in returns]
        if not codes or len(dates) < self.MIN_OBS:
            return {"status": "insufficient_data", "obs_days": len(dates), "codes_used": codes,
                    "message": f"公共交易日仅{len(dates)}天（需≥{self.MIN_OBS}），VaR 暂不可用"}

        horizon = max(1.0, float(horizon))
        conf = min(0.999, max(0.5, float(confidence)))
        weights = {c: float(allocation.get(c) or 0) for c in codes}
        port = [sum(weights[c] * returns[c][i] for c in codes) for i in range(len(dates))]
        ordered = sorted(port)
        idx = min(len(ordered) - 1, max(0, int(math.floor((1 - conf) * len(ordered)))))
        var_1d = -ordered[idx]
        var_h = var_1d * math.sqrt(horizon)      # 按 √t 时间缩放（隐含独立同分布假设）
        worst_i = port.index(ordered[idx])
        tail = ordered[:idx + 1]
        es = -sum(tail) / len(tail)
        return {
            "status": "ok",
            "obs_days": len(dates),
            "confidence": conf,
            "horizon_days": horizon,
            "var_pct": round(var_h, 4),
            "var_amount": round(var_h * (base_value or 0), 2),
            "expected_shortfall_pct": round(es, 4),
            "base_value": round(base_value or 0, 2),
            "var_day": {"trade_date": dates[worst_i].isoformat(),
                        "return_pct": round(ordered[idx], 4)},
            "codes_used": codes,
            "text": (f"近{len(dates)}个交易日：{conf:.0%}置信度下{horizon:g}日 VaR "
                     f"{var_h:.2%}（约{var_h * (base_value or 0):,.0f}元），尾部均值(ES) {es:.2%}"),
        }

    def _var_of_allocation(self, allocation: Dict[str, float], db: Session,
                           horizon: float = 1, confidence: float = 0.95,
                           window: int = PRICE_WINDOW,
                           base_value: Optional[float] = None,
                           strategy_id: Optional[int] = None) -> Dict:
        codes = [c for c in allocation if c]
        if not codes:
            return {"status": "insufficient", "message": "组合为空，VaR 不可算"}
        dates, returns = self._grid_for(codes, db, window)
        if base_value is None:
            base_value = self._portfolio_value(strategy_id, db)
        return self._var_on_grid(allocation, dates, returns, horizon=horizon,
                                 confidence=confidence, base_value=base_value)

    def _portfolio_value(self, strategy_id: Optional[int], db: Session) -> float:
        """最新净值快照的总资产（无快照时回退初始资金）"""
        if strategy_id is None:
            return 0.0
        snap = (db.query(PortfolioSnapshot)
                .filter(PortfolioSnapshot.strategy_id == strategy_id)
                .order_by(PortfolioSnapshot.trade_date.desc()).first())
        if snap and snap.total_asset:
            return float(snap.total_asset)
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        return float(strategy.initial_capital or 0) if strategy else 0.0

    def get_var(self, strategy_id: int, db: Session, horizon: int = 1,
                confidence: float = 0.95, window: int = PRICE_WINDOW) -> Dict:
        """组合在险价值（VaR）：当前生效配置的合成收益序列分位损失"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "error", "message": "策略不存在"}
        allocation = self._effective_allocation(strategy)
        result = self._var_of_allocation(
            allocation, db, horizon=horizon, confidence=confidence, window=window,
            strategy_id=strategy_id,
        )
        result["strategy_id"] = strategy_id
        return result

    def run_stress_test(self, strategy_id: int, db: Session,
                        scenario: Optional[Dict] = None) -> Dict:
        """运行压力测试（按 allocation_config 加权，区分资产类别敏感度）

        scenario: 自定义跌幅场景 {"name": 场景名, "shock": 跌幅}，提供时**替换**预设场景
                  （问什么答什么，避免把 4 个预设一起倒给 LLM）；缺省沿用预设四场景。
        """
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "error", "message": "策略不存在"}

        allocation = strategy.allocation_config or {}

        # 查询 ETF 名称用于资产类别分类
        etf_names = {
            e.etf_code: e.etf_name for e in
            db.query(ETFBasic).filter(ETFBasic.etf_code.in_(allocation.keys())).all()
        } if allocation else {}

        scenarios = [
            {"name": "市场暴跌", "shock": -0.20, "description": "模拟市场整体下跌20%"},
            {"name": "金融危机", "shock": -0.30, "description": "模拟极端金融危机场景"},
            {"name": "流动性危机", "shock": -0.15, "description": "模拟流动性枯竭"},
            {"name": "政策冲击", "shock": -0.10, "description": "模拟政策重大变化"},
        ]
        if scenario is not None:
            parsed = self._parse_custom_scenario(scenario)
            if isinstance(parsed, str):
                return {"status": "error", "message": parsed}
            scenarios = [parsed]

        initial_capital = strategy.initial_capital or 100000

        results = []
        for item in scenarios:
            base_shock = item["shock"]

            # 按配置比例加权，不同资产类别敏感度不同
            weighted_shock = 0.0
            for code, ratio in allocation.items():
                name = etf_names.get(code, "")
                asset_multiplier = self._classify_etf_shock_multiplier(name)
                weighted_shock += base_shock * asset_multiplier * ratio

            estimated_loss = initial_capital * weighted_shock
            estimated_final_value = initial_capital + estimated_loss

            severity = "critical" if abs(weighted_shock) > 0.25 else "severe" if abs(weighted_shock) > 0.15 else "moderate"

            results.append({
                "scenario": item["name"],
                "description": item["description"],
                "shock_pct": round(weighted_shock, 4),
                "estimated_loss": round(estimated_loss, 2),
                "estimated_final_value": round(estimated_final_value, 2),
                "severity": severity,
                "recovery_time_estimate": self._estimate_recovery_time(abs(weighted_shock)),
            })

        worst_case = min(results, key=lambda x: x["estimated_final_value"])

        return {
            "status": "completed",
            "scenarios": results,
            "worst_case": worst_case,
            "risk_assessment": self._generate_risk_assessment(worst_case),
            "recommendations": self._generate_risk_recommendations(results),
        }

    @staticmethod
    def _parse_custom_scenario(scenario: Dict) -> object:
        """规范化自定义跌幅场景，非法时返回错误说明字符串"""
        name = str(scenario.get("name") or "").strip() or "自定义场景"
        try:
            shock = -abs(float(scenario.get("shock")))
        except (TypeError, ValueError):
            return "shock 需为跌幅数值（如 -0.15 或 0.15）"
        if not 0 < abs(shock) <= 0.99:
            return f"跌幅 {abs(shock):.2%} 不在 (0, 99%] 区间"
        return {
            "name": name[:30],
            "shock": round(shock, 4),
            "description": str(scenario.get("description") or f"自定义场景：整体下跌{abs(shock):.0%}")[:80],
        }

    def _classify_etf_shock_multiplier(self, etf_name: str) -> float:
        """根据 ETF 名称推断资产类别敏感度倍数（相对股票全仓的波动比例）"""
        name = etf_name or ""
        if any(k in name for k in ["债", "国债", "信用", "利率", "短融"]):
            return 0.3
        if any(k in name for k in ["黄金", "金", "贵金属"]):
            return 0.8
        if any(k in name for k in ["货币", "现金"]):
            return 0.0
        return 1.0
    
    def _estimate_recovery_time(self, loss_pct: float) -> str:
        """估算恢复时间"""
        avg_monthly_return = 0.02
        
        months_needed = abs(loss_pct) / avg_monthly_return
        
        if months_needed < 3:
            return "1-3个月"
        elif months_needed < 6:
            return "3-6个月"
        elif months_needed < 12:
            return "6-12个月"
        else:
            return "超过1年"
    
    def _generate_risk_assessment(self, worst_case: Dict) -> str:
        """生成风险评估"""
        severity = worst_case["severity"]
        
        if severity == "critical":
            return f"极端风险: {worst_case['scenario']}下可能损失{abs(worst_case['shock_pct']):.0%}，建议立即优化配置"
        elif severity == "severe":
            return f"高风险: {worst_case['scenario']}下可能损失{abs(worst_case['shock_pct']):.0%}，需加强风险防范"
        else:
            return f"中等风险: 当前配置在压力测试下表现稳健"
    
    def _generate_risk_recommendations(self, results: List[Dict]) -> List[str]:
        """生成风险建议"""
        recommendations = []
        
        critical_scenarios = [r for r in results if r["severity"] == "critical"]
        if critical_scenarios:
            recommendations.append("建议降低整体仓位，应对极端场景")
        
        severe_scenarios = [r for r in results if r["severity"] == "severe"]
        if severe_scenarios:
            recommendations.append("建议增加避险资产配置（如债券ETF）")
        
        long_recovery = [r for r in results if "超过1年" in r["recovery_time_estimate"]]
        if long_recovery:
            recommendations.append("建议设置止损线，控制最大损失")
        
        if not recommendations:
            recommendations.append("当前风险水平适中，建议维持现状")
        
        return recommendations
    
    def get_risk_dashboard(self, strategy_id: int, db: Session) -> Dict:
        """获取风险仪表盘"""
        circuit_breaker = self.check_circuit_breaker(strategy_id, db)
        drawdown = self.apply_drawdown_protection(strategy_id, db)
        budget = self.check_risk_budget(strategy_id, db)
        stress_test = self.run_stress_test(strategy_id, db)
        
        overall_risk_level = self._calculate_overall_risk_level(
            circuit_breaker, drawdown, budget
        )
        
        return {
            "strategy_id": strategy_id,
            "overall_risk_level": overall_risk_level,
            "circuit_breaker": circuit_breaker,
            "drawdown_protection": drawdown,
            "risk_budget": budget,
            "stress_test_summary": {
                "worst_case": stress_test["worst_case"],
                "risk_assessment": stress_test["risk_assessment"],
            },
            "risk_alerts": self._collect_risk_alerts(circuit_breaker, drawdown, budget),
            "timestamp": date.today().isoformat(),
        }
    
    def _calculate_overall_risk_level(
        self,
        circuit_breaker: Dict,
        drawdown: Dict,
        budget: Dict
    ) -> str:
        """计算整体风险等级"""
        risk_score = 0
        
        if circuit_breaker.get("status") == "triggered":
            risk_score += 3
        
        if drawdown.get("status") == "critical":
            risk_score += 3
        elif drawdown.get("status") == "warning":
            risk_score += 2
        elif drawdown.get("status") == "alert":
            risk_score += 1
        
        if budget.get("status") == "violation":
            risk_score += 1
        
        if risk_score >= 5:
            return "critical"
        elif risk_score >= 3:
            return "high"
        elif risk_score >= 1:
            return "medium"
        else:
            return "low"
    
    def _collect_risk_alerts(
        self,
        circuit_breaker: Dict,
        drawdown: Dict,
        budget: Dict
    ) -> List[str]:
        """收集风险警报"""
        alerts = []
        
        if circuit_breaker.get("status") == "triggered":
            alerts.append(f"⚠️ 熔断触发: {circuit_breaker['reason']}")
        
        if drawdown.get("status") in ["critical", "warning", "alert"]:
            alerts.append(f"📉 回撤警报: {drawdown['message']}")
        
        if budget.get("status") == "violation":
            for v in budget.get("violations", []):
                alerts.append(f"⚠️ {v['message']}")
        
        return alerts
    
    def should_pause_strategy(self, strategy_id: int, db: Session) -> bool:
        """判断是否应暂停策略"""
        circuit_breaker = self.check_circuit_breaker(strategy_id, db)
        
        if circuit_breaker.get("status") == "triggered":
            strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
            if strategy and strategy.auto_strategy_status == "running":
                strategy.auto_strategy_status = "paused"
                strategy.paused_reason = circuit_breaker.get("reason")
                strategy.paused_date = date.today()
                strategy.paused_cooldown_days = circuit_breaker.get("cooldown_days")
                db.commit()
                
                logger.warning(f"策略{strategy_id}因{circuit_breaker['reason']}已暂停")
                return True
        
        return False

_service = None


def get_risk_controller() -> RiskController:
    global _service
    if _service is None:
        _service = RiskController()
    return _service
