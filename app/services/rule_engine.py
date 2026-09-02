"""
规则驱动回测引擎 - 技术指标→市场状态→配置比例
支持两种模式：
1. 确定性规则（Phase 1）：硬编码映射表
2. AI历史规则（Phase 2）：从 auto_strategy_log 提取的 regime→allocation 映射
"""

import logging
from datetime import date, timedelta
from typing import Dict, Optional

import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func

from app.models.etf import ETFQuotation, ETFDailyIndicator

logger = logging.getLogger(__name__)

# ============================================================
# Phase 1: 确定性规则配置表
# ============================================================

REGIMEAllocation = {
    "bull_strong": {"equity": 0.85, "bond": 0.10, "gold": 0.05},
    "bull_weak":   {"equity": 0.70, "bond": 0.20, "gold": 0.10},
    "neutral":     {"equity": 0.50, "bond": 0.30, "gold": 0.20},
    "bear_weak":   {"equity": 0.30, "bond": 0.40, "gold": 0.30},
    "bear_strong": {"equity": 0.15, "bond": 0.50, "gold": 0.35},
}

REGIME_LABELS = {
    "bull_strong": "强势牛市",
    "bull_weak": "弱牛市",
    "neutral": "震荡市",
    "bear_weak": "弱熊市",
    "bear_strong": "强势熊市",
    "bull_volatile": "震荡牛市",
    "bull_quiet": "温和牛市",
}

ETF_CATEGORIES = {
    "equity": ["510300", "510500", "510050", "159915", "512100", "588000"],
    "bond": ["511010", "511260", "511020"],
    "gold": ["518880", "518850", "159934"],
}

_ETF_TO_CATEGORY = {}
for cat, codes in ETF_CATEGORIES.items():
    for c in codes:
        _ETF_TO_CATEGORY[c] = cat


def _classify_etf(code: str) -> str:
    return _ETF_TO_CATEGORY.get(code, "equity")


class RuleEngine:
    """
    规则驱动配置生成器
    
    支持两种规则来源：
    - 确定性规则：硬编码的 regime→allocation 映射
    - AI历史规则：从 auto_strategy_log 提取的 regime→allocation 映射
    
    规则分层（策略化）：
    - regime 判定全局共享；regime→配置映射按策略统计
    - 兜底链：本策略AI规则 → 全局AI规则 → 确定性规则 → 策略静态配置
    - 所有分支产出统一收敛到策略标的池
    """

    # 落库快照新鲜度（天数）：超期视为陈旧，现场重算
    SNAPSHOT_FRESH_DAYS = 7
    # AI规则数据量门槛：样本天数不足时回退
    MIN_RULE_DAYS = 10

    def __init__(self):
        # 分层规则缓存，key: "global" / "strategy:{id}"
        self._trained_by_scope: Dict[str, dict] = {}

    def _ensure_trained_rules(self, db: Session, strategy_id: Optional[int] = None) -> Optional[dict]:
        """确保已加载训练规则（策略优先，全局兜底；优先读落库快照，超期现场重算）"""
        scope = f"strategy:{strategy_id}" if strategy_id is not None else "global"
        if scope in self._trained_by_scope:
            return self._trained_by_scope[scope]

        rules = self._load_fresh_snapshot(db, strategy_id) or self._train_and_snapshot(db, strategy_id)
        rules = self._check_min_days(rules)
        if rules:
            self._trained_by_scope[scope] = rules
            return rules

        # 本策略规则数据不足 → 回退全局规则
        if strategy_id is not None:
            return self._ensure_trained_rules(db, strategy_id=None)

        logger.info("[RuleEngine] AI历史数据不足，使用确定性规则")
        return None

    def _load_fresh_snapshot(self, db: Session, strategy_id: Optional[int]) -> Optional[dict]:
        """读取新鲜期内的落库规则快照"""
        try:
            from datetime import timedelta
            from app.models.strategy import RuleSnapshot

            cutoff = date.today() - timedelta(days=self.SNAPSHOT_FRESH_DAYS)
            query = db.query(RuleSnapshot).filter(RuleSnapshot.created_at >= cutoff)
            query = query.filter(RuleSnapshot.strategy_id.is_(None) if strategy_id is None
                                 else RuleSnapshot.strategy_id == strategy_id)
            row = query.order_by(RuleSnapshot.created_at.desc()).first()
            if row:
                logger.info(f"[RuleEngine] 命中规则快照: strategy={strategy_id or 'global'} @ {row.created_at}")
                return row.snapshot
        except Exception as e:
            logger.warning(f"[RuleEngine] 读取规则快照失败: {e}")
        return None

    def _train_and_snapshot(self, db: Session, strategy_id: Optional[int]) -> Optional[dict]:
        """现场训练规则并落一份快照（source=manual）"""
        try:
            from app.services.rule_trainer import get_rule_trainer
            rules = get_rule_trainer().train(db, days=90, strategy_id=strategy_id)
            try:
                from app.models.strategy import RuleSnapshot
                db.add(RuleSnapshot(
                    strategy_id=strategy_id,
                    snapshot=rules,
                    source="manual",
                    days_covered=(rules.get("training_period") or {}).get("days"),
                ))
                db.commit()
            except Exception as e:
                db.rollback()
                logger.warning(f"[RuleEngine] 规则快照落库失败: {e}")
            return rules
        except Exception as e:
            logger.warning(f"[RuleEngine] 训练AI历史规则失败: {e}")
            return None

    def _check_min_days(self, rules: Optional[dict]) -> Optional[dict]:
        """数据量门槛检查"""
        if not rules:
            return None
        days = (rules.get("training_period") or {}).get("days", 0)
        if days >= self.MIN_RULE_DAYS:
            logger.info("[RuleEngine] 加载AI历史规则[%s]: %d天数据, %d种regime" % (
                rules.get("scope", "?"), days, len(rules.get("regime_rules", {}))
            ))
            return rules
        logger.info(f"[RuleEngine] AI历史数据不足({days}天): scope={rules.get('scope', '?')}")
        return None

    def get_rules(self, db: Session, strategy_id: Optional[int] = None) -> Optional[dict]:
        """获取规则表（优先落库快照，现场计算兜底；供复盘进化等只读方使用）"""
        return self._ensure_trained_rules(db, strategy_id=strategy_id)

    def invalidate_cache(self):
        """清除规则缓存（新分析完成后调用）"""
        self._trained_by_scope = {}
        logger.info("[RuleEngine] 规则缓存已清除")

    def compute_daily_allocation(
        self,
        trade_date: date,
        db: Session,
        base_allocation: dict,
        lookback_days: int = 30,
        strategy_id: Optional[int] = None,
    ) -> dict:
        """
        根据当日技术指标计算目标配置
        
        规则兜底链：本策略AI规则 → 全局AI规则 → 确定性规则 → 策略静态配置；
        所有AI/确定性分支产出统一收敛到策略标的池（base_allocation 的 keys）。
        """
        # 1. 获取当日有指标的ETF
        indicators = self._get_indicators(trade_date, db)
        if not indicators:
            return base_allocation
        pool = set((base_allocation or {}).keys())

        # 2. 判定市场状态（全局共享）
        regime = self._compute_regime(trade_date, db, lookback_days)
        
        # 3. AI历史规则（策略优先，全局兜底）
        trained = self._ensure_trained_rules(db, strategy_id=strategy_id)
        if trained:
            from app.services.rule_trainer import get_rule_trainer
            trainer = get_rule_trainer()
            ai_alloc = trainer.get_allocation_for_regime(regime, trained)
            
            if ai_alloc and sum(ai_alloc.values()) > 0:
                # 只保留当日有指标的ETF，并收敛到策略标的池
                allocation = {
                    etf: weight for etf, weight in ai_alloc.items()
                    if any(ind.etf_code == etf for ind in indicators)
                }
                allocation = self._filter_to_pool(allocation, pool)
                if allocation:
                    return allocation
        
        # 4. 回退到确定性规则（同样收敛到策略标的池）
        regime_weights = REGIMEAllocation.get(regime, REGIMEAllocation["neutral"])
        pool_indicators = [ind for ind in indicators if not pool or ind.etf_code in pool]
        allocation = self._distribute_by_score(pool_indicators, regime_weights)
        allocation = self._filter_to_pool(allocation, pool)
        if allocation:
            return allocation

        # 5. 最终兜底：策略静态配置
        return base_allocation

    @staticmethod
    def _filter_to_pool(allocation: dict, pool: set) -> dict:
        """把配置收敛到策略标的池内并归一化；池外标的剔除，结果为空返回{}"""
        if not allocation:
            return {}
        if not pool:
            return allocation
        filtered = {k: v for k, v in allocation.items() if k in pool and v > 0}
        total = sum(filtered.values())
        if total <= 0:
            return {}
        return {k: round(v / total, 4) for k, v in filtered.items()}

    def _get_indicators(self, trade_date: date, db: Session) -> list:
        rows = (
            db.query(ETFDailyIndicator)
            .filter(ETFDailyIndicator.trade_date == trade_date)
            .all()
        )
        return rows

    def _compute_regime(self, trade_date: date, db: Session, lookback: int = 30) -> str:
        start_date = trade_date - timedelta(days=lookback + 10)

        rows = (
            db.query(
                sa_func.avg(ETFDailyIndicator.composite_score),
                sa_func.avg(ETFDailyIndicator.volatility_20d),
                sa_func.avg(ETFDailyIndicator.momentum_5d),
                sa_func.avg(ETFDailyIndicator.momentum_20d),
            )
            .filter(
                ETFDailyIndicator.trade_date >= start_date,
                ETFDailyIndicator.trade_date <= trade_date,
            )
            .first()
        )

        avg_score = float(rows[0] or 50)
        avg_vol = float(rows[1] or 15)
        avg_mom5 = float(rows[2] or 0)
        avg_mom20 = float(rows[3] or 0)

        composite = (
            avg_score * 0.4
            + (avg_mom5 + avg_mom20) * 3
            - avg_vol * 0.5
        )

        if composite >= 70:
            return "bull_strong"
        elif composite >= 55:
            return "bull_weak"
        elif composite >= 40:
            return "neutral"
        elif composite >= 25:
            return "bear_weak"
        else:
            return "bear_strong"

    def _distribute_by_score(
        self,
        indicators: list,
        regime_weights: dict
    ) -> dict:
        by_category = {"equity": [], "bond": [], "gold": []}
        for ind in indicators:
            cat = _classify_etf(ind.etf_code)
            by_category[cat].append(ind)

        allocation = {}
        for cat, target_weight in regime_weights.items():
            items = by_category.get(cat, [])
            if not items:
                continue
            items.sort(key=lambda x: x.composite_score or 0, reverse=True)
            max_etfs = {"equity": 4, "bond": 2, "gold": 2}
            items = items[:max_etfs.get(cat, 3)]
            if not items:
                continue
            scores = np.array([max(ind.composite_score or 10, 1) for ind in items])
            exp_scores = np.exp(scores - np.max(scores))
            weights = exp_scores / exp_scores.sum()
            for ind, w in zip(items, weights):
                allocation[ind.etf_code] = round(float(w * target_weight), 4)

        total = sum(allocation.values())
        if total > 0:
            allocation = {k: round(v / total, 4) for k, v in allocation.items()}
        return allocation

    def get_regime_info(
        self,
        trade_date: date,
        db: Session,
        lookback: int = 30,
        strategy_id: Optional[int] = None,
    ) -> dict:
        regime = self._compute_regime(trade_date, db, lookback)
        regime_weights = REGIMEAllocation.get(regime, REGIMEAllocation["neutral"])

        start_date = trade_date - timedelta(days=lookback + 10)
        rows = (
            db.query(
                sa_func.avg(ETFDailyIndicator.composite_score),
                sa_func.avg(ETFDailyIndicator.volatility_20d),
                sa_func.avg(ETFDailyIndicator.momentum_5d),
                sa_func.avg(ETFDailyIndicator.momentum_20d),
            )
            .filter(
                ETFDailyIndicator.trade_date >= start_date,
                ETFDailyIndicator.trade_date <= trade_date,
            )
            .first()
        )

        # 检查是否有AI历史规则（策略优先，标注规则来源供前端展示）
        trained = self._ensure_trained_rules(db, strategy_id=strategy_id)
        rule_source = "deterministic"
        regime_explanation = ""
        if trained:
            scope = trained.get("scope", "global")
            if trained.get("regime_rules", {}).get(regime):
                rule_source = "ai_history_strategy" if scope.startswith("strategy:") else "ai_history_global"
                from app.services.rule_trainer import get_rule_trainer
                regime_explanation = get_rule_trainer().explain_regime(regime, trained)
            elif scope.startswith("strategy:"):
                # 本策略无该regime但全局有 → 降级标注
                rule_source = "ai_history_global"

        return {
            "regime": regime,
            "regime_label": REGIME_LABELS.get(regime, "未知"),
            "avg_score": round(float(rows[0] or 50), 1),
            "avg_volatility": round(float(rows[1] or 15), 1),
            "avg_momentum_5d": round(float(rows[2] or 0), 2),
            "avg_momentum_20d": round(float(rows[3] or 0), 2),
            "target_weights": regime_weights,
            "rule_source": rule_source,
            "regime_explanation": regime_explanation,
        }


_rule_engine = None

def get_rule_engine() -> RuleEngine:
    global _rule_engine
    if _rule_engine is None:
        _rule_engine = RuleEngine()
    return _rule_engine
