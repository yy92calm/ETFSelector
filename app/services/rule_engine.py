"""
规则驱动回测引擎 - 技术指标→市场状态→配置比例
确定性规则，不依赖LLM，可覆盖2020-2026全量行情
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
# 规则配置表
# ============================================================

# 市场状态 → 权益/债券/黄金配置比例
REGIMEAllocation = {
    "bull_strong": {"equity": 0.85, "bond": 0.10, "gold": 0.05},
    "bull_weak":   {"equity": 0.70, "bond": 0.20, "gold": 0.10},
    "neutral":     {"equity": 0.50, "bond": 0.30, "gold": 0.20},
    "bear_weak":   {"equity": 0.30, "bond": 0.40, "gold": 0.30},
    "bear_strong": {"equity": 0.15, "bond": 0.50, "gold": 0.35},
}

# ETF分类（需与实际ETF代码对应）
ETF_CATEGORIES = {
    # 权益类ETF（按大盘/中小盘/行业分散）
    "equity": [
        "510300",  # 沪深300ETF
        "510500",  # 中证500ETF
        "510050",  # 上证50ETF
        "159915",  # 创业板ETF
        "512100",  # 中证1000ETF
        "588000",  # 科创50ETF
    ],
    # 债券类ETF
    "bond": [
        "511010",  # 国债ETF
        "511260",  # 十年国债ETF
        "511020",  # 活跃国债ETF
    ],
    # 黄金类ETF
    "gold": [
        "518880",  # 黄金ETF
        "518850",  # 黄金ETF（华安）
        "159934",  # 黄金ETF（易方达）
    ],
}

# 构建反向查找表：etf_code → category
_ETF_TO_CATEGORY = {}
for cat, codes in ETF_CATEGORIES.items():
    for c in codes:
        _ETF_TO_CATEGORY[c] = cat


def _classify_etf(code: str) -> str:
    """判断ETF属于哪个类别"""
    return _ETF_TO_CATEGORY.get(code, "equity")  # 默认归为权益类


class RuleEngine:
    """
    规则驱动配置生成器
    
    输入：某一日的技术指标数据
    输出：当日目标 allocation_config
    
    逻辑：
    1. 取权重最大的N只ETF的综合指标
    2. 判定市场状态（regime）
    3. 按规则表生成目标配置比例
    """

    def compute_daily_allocation(
        self,
        trade_date: date,
        db: Session,
        base_allocation: dict,
        lookback_days: int = 30,
    ) -> dict:
        """
        根据当日技术指标计算目标配置
        
        Args:
            trade_date: 交易日
            db: 数据库会话
            base_allocation: 默认配置（fallback）
            lookback_days: 回看天数（用于计算趋势和波动率）
        
        Returns:
            {etf_code: weight} 配置比例字典，总和=1.0
        """
        # 1. 获取当日有指标的ETF
        indicators = self._get_indicators(trade_date, db)
        if not indicators:
            return base_allocation

        # 2. 计算市场综合状态
        regime = self._compute_regime(trade_date, db, lookback_days)
        
        # 3. 按regime确定各类资产权重
        regime_weights = REGIMEAllocation.get(regime, REGIMEAllocation["neutral"])
        
        # 4. 在每个类别内，按composite_score分配权重
        allocation = self._distribute_by_score(indicators, regime_weights)
        
        return allocation

    def _get_indicators(self, trade_date: date, db: Session) -> list:
        """获取当日所有ETF的技术指标"""
        rows = (
            db.query(ETFDailyIndicator)
            .filter(ETFDailyIndicator.trade_date == trade_date)
            .all()
        )
        return rows

    def _compute_regime(self, trade_date: date, db: Session, lookback: int = 30) -> str:
        """
        判定市场状态
        
        规则：
        - 取近30日的composite_score趋势
        - 取近30日的平均波动率
        - 取近5日 vs 近20日动量对比
        
        返回: bull_strong / bull_weak / neutral / bear_weak / bear_strong
        """
        from datetime import date as _date
        start_date = trade_date - timedelta(days=lookback + 10)  # 多取几天容错

        # 取近30日所有ETF的指标
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

        # 综合评分（0-100）
        # score越高=越看多，vol越高=越看空
        composite = (
            avg_score * 0.4              # 综合得分（0-100）
            + (avg_mom5 + avg_mom20) * 3  # 动量加分（±30）
            - avg_vol * 0.5              # 波动率惩罚（-15~0）
        )

        # 映射到regime
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
        """
        在每个资产类别内，按composite_score分配权重
        
        逻辑：
        1. 按类别分组
        2. 组内按composite_score排序
        3. 得分高的获得更多权重（ softmax 归一化）
        """
        # 按类别分组
        by_category = {"equity": [], "bond": [], "gold": []}
        for ind in indicators:
            cat = _classify_etf(ind.etf_code)
            by_category[cat].append(ind)

        allocation = {}

        for cat, target_weight in regime_weights.items():
            items = by_category.get(cat, [])
            if not items:
                continue

            # 按composite_score排序
            items.sort(key=lambda x: x.composite_score or 0, reverse=True)

            # 取前N只（避免过度分散）
            max_etfs = {"equity": 4, "bond": 2, "gold": 2}
            items = items[:max_etfs.get(cat, 3)]

            if not items:
                continue

            # 按score分配权重（score越高权重越大）
            scores = np.array([max(ind.composite_score or 10, 1) for ind in items])
            # softmax归一化
            exp_scores = np.exp(scores - np.max(scores))
            weights = exp_scores / exp_scores.sum()

            for ind, w in zip(items, weights):
                allocation[ind.etf_code] = round(float(w * target_weight), 4)

        # 归一化到1.0
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
        """
        获取市场状态详情（供前端展示）
        """
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

        return {
            "regime": regime,
            "regime_label": {
                "bull_strong": "强势牛市",
                "bull_weak": "弱牛市",
                "neutral": "震荡市",
                "bear_weak": "弱熊市",
                "bear_strong": "强势熊市",
            }.get(regime, "未知"),
            "avg_score": round(float(rows[0] or 50), 1),
            "avg_volatility": round(float(rows[1] or 15), 1),
            "avg_momentum_5d": round(float(rows[2] or 0), 2),
            "avg_momentum_20d": round(float(rows[3] or 0), 2),
            "target_weights": regime_weights,
        }


# 单例
_rule_engine = None

def get_rule_engine() -> RuleEngine:
    global _rule_engine
    if _rule_engine is None:
        _rule_engine = RuleEngine()
    return _rule_engine
