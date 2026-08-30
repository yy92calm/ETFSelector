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
    """

    def __init__(self):
        self._trained_rules = None  # 缓存训练后的规则
        self._rules_date = None

    def _ensure_trained_rules(self, db: Session) -> Optional[dict]:
        """确保已加载训练规则"""
        if self._trained_rules is not None:
            return self._trained_rules
        
        try:
            from app.services.rule_trainer import get_rule_trainer
            trainer = get_rule_trainer()
            rules = trainer.train(db, days=90)
            
            # 只有当规则表有足够数据时才使用
            if rules.get("training_period") and rules["training_period"]["days"] >= 10:
                self._trained_rules = rules
                self._rules_date = date.today()
                logger.info("[RuleEngine] 加载AI历史规则: %d天数据, %d种regime" % (
                    rules["training_period"]["days"],
                    len(rules.get("regime_rules", {}))
                ))
                return rules
            else:
                logger.info("[RuleEngine] AI历史数据不足(%d天)，使用确定性规则" % (
                    rules.get("training_period", {}).get("days", 0) if rules.get("training_period") else 0
                ))
                return None
        except Exception as e:
            logger.warning("[RuleEngine] 加载AI历史规则失败: %s，回退确定性规则" % e)
            return None

    def invalidate_cache(self):
        """清除规则缓存（新分析完成后调用）"""
        self._trained_rules = None
        self._rules_date = None
        logger.info("[RuleEngine] 规则缓存已清除")

    def compute_daily_allocation(
        self,
        trade_date: date,
        db: Session,
        base_allocation: dict,
        lookback_days: int = 30,
    ) -> dict:
        """
        根据当日技术指标计算目标配置
        
        优先使用AI历史规则，回退到确定性规则
        """
        # 1. 获取当日有指标的ETF
        indicators = self._get_indicators(trade_date, db)
        if not indicators:
            return base_allocation

        # 2. 判定市场状态
        regime = self._compute_regime(trade_date, db, lookback_days)
        
        # 3. 尝试用AI历史规则
        trained = self._ensure_trained_rules(db)
        if trained:
            from app.services.rule_trainer import get_rule_trainer
            trainer = get_rule_trainer()
            ai_alloc = trainer.get_allocation_for_regime(regime, trained)
            
            if ai_alloc and sum(ai_alloc.values()) > 0:
                # 用AI历史规则的平均配置，但只保留当日有指标的ETF
                allocation = {}
                for etf, weight in ai_alloc.items():
                    if any(ind.etf_code == etf for ind in indicators):
                        allocation[etf] = weight
                
                # 归一化
                total = sum(allocation.values())
                if total > 0:
                    allocation = {k: round(v / total, 4) for k, v in allocation.items()}
                    return allocation
        
        # 4. 回退到确定性规则
        regime_weights = REGIMEAllocation.get(regime, REGIMEAllocation["neutral"])
        allocation = self._distribute_by_score(indicators, regime_weights)
        return allocation

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
        lookback: int = 30
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

        # 检查是否有AI历史规则
        trained = self._ensure_trained_rules(db)
        rule_source = "deterministic"
        regime_explanation = ""
        if trained and trained.get("regime_rules", {}).get(regime):
            rule_source = "ai_history"
            from app.services.rule_trainer import get_rule_trainer
            regime_explanation = get_rule_trainer().explain_regime(regime, trained)

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
