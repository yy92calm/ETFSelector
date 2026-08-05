"""因子表现服务 - 计算IC（信息系数）、自适应权重"""

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.factor_performance import FactorPerformance
from app.models.etf import ETFQuotation

logger = logging.getLogger(__name__)

# 与 market_scanner_service.WEIGHTS 保持一致
DEFAULT_WEIGHTS = {
    "momentum": 0.35,
    "trend": 0.20,
    "volume": 0.15,
    "volatility": 0.15,
    "capital_flow": 0.15,
}

FACTORS = list(DEFAULT_WEIGHTS.keys())


class FactorPerformanceService:
    """因子表现跟踪 - 记录因子值与未来收益，计算IC，动态调整权重"""

    def backfill_from_indicators(self, db: Session) -> int:
        """从已有 ETFDailyIndicator 重建因子得分并写入 factor_performance

        用于首次上线时补齐历史因子数据（momentum/trend/volume/volatility/capital_flow）。
        """
        from app.models.etf import ETFDailyIndicator

        existing = set(db.query(FactorPerformance.factor_name).distinct().all())
        if existing:
            # 已有数据则跳过（避免重复）
            existing_dates = set(r[0] for r in db.query(FactorPerformance.trade_date).distinct().all())
        else:
            existing_dates = set()

        indicators = db.query(ETFDailyIndicator).all()
        if not indicators:
            return 0

        from app.services.market_scanner_service import get_market_scanner_service
        scanner = get_market_scanner_service()

        added = 0
        for ind in indicators:
            if ind.trade_date in existing_dates:
                continue
            scores = {
                "momentum": scanner._normalize_momentum(ind.momentum_score or 0),
                "trend": (ind.trend_strength or 0) / 3.0 * 100,
                "volume": scanner._volume_score(ind.vol_ratio or 1.0, ind.momentum_5d or 0),
                "volatility": scanner._volatility_score(ind.volatility_20d or 0),
                "capital_flow": scanner._flow_score(ind.obv_slope or 0, ind.amount_avg_5d or 0),
            }
            for fname, score in scores.items():
                db.add(FactorPerformance(
                    etf_code=ind.etf_code,
                    trade_date=ind.trade_date,
                    factor_name=fname,
                    factor_value=round(score, 4),
                ))
                added += 1

        if added > 0:
            db.commit()
            logger.info(f"[FactorPerf] 从指标表重建 {added} 条因子记录")
        return added

    def backfill_forward_returns(self, db: Session):
        """回填未来5日收益率：为所有未回填的记录计算 forward_return_5d

        未来5日收益 = (T+5日收盘 - T日收盘) / T日收盘 * 100
        只有 T+5 日有行情数据的记录才回填。
        """
        pending = db.query(FactorPerformance).filter(
            FactorPerformance.forward_return_5d == None
        ).order_by(FactorPerformance.trade_date.desc()).limit(2000).all()

        if not pending:
            return 0

        dates = sorted({p.trade_date for p in pending})
        filled = 0
        for p in pending:
            # 找到 T+5 日的收盘价
            future_quotes = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == p.etf_code,
                ETFQuotation.trade_date > p.trade_date,
            ).order_by(ETFQuotation.trade_date.asc()).limit(5).all()

            if len(future_quotes) < 5:
                continue

            base_price = None
            base_q = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == p.etf_code,
                ETFQuotation.trade_date == p.trade_date,
            ).first()
            if base_q:
                base_price = base_q.close_price

            target_price = future_quotes[4].close_price
            if base_price and base_price > 0:
                p.forward_return_5d = round((target_price - base_price) / base_price * 100, 4)
                filled += 1

        if filled > 0:
            db.commit()
            logger.info(f"[FactorPerf] 回填 {filled} 条未来5日收益")
        return filled

    def compute_daily_ic(self, target_date: date, db: Session) -> Dict[str, float]:
        """计算某日各因子的截面IC（Spearman秩相关：因子值 vs 未来5日收益）"""
        rows = db.query(FactorPerformance).filter(
            FactorPerformance.trade_date == target_date,
            FactorPerformance.forward_return_5d != None,
        ).all()

        factor_data: Dict[str, List[tuple]] = defaultdict(list)
        for r in rows:
            factor_data[r.factor_name].append((r.factor_value, r.forward_return_5d))

        ic_results = {}
        for factor, pairs in factor_data.items():
            if len(pairs) < 10:  # 样本不足不计算
                continue
            values = np.array([p[0] for p in pairs], dtype=float)
            returns = np.array([p[1] for p in pairs], dtype=float)

            # 处理NaN
            valid = ~(np.isnan(values) | np.isnan(returns))
            if valid.sum() < 10:
                continue

            values = values[valid]
            returns = returns[valid]

            # Spearman 秩相关
            v_rank = self._rankdata(values)
            r_rank = self._rankdata(returns)
            corr = np.corrcoef(v_rank, r_rank)[0, 1]
            if np.isnan(corr):
                continue
            ic_results[factor] = round(float(corr), 4)

        return ic_results

    @staticmethod
    def _rankdata(arr: np.ndarray) -> np.ndarray:
        """计算数组的秩（处理并列值）"""
        order = np.argsort(arr)
        ranks = np.empty(len(arr), dtype=float)
        ranks[order] = np.arange(1, len(arr) + 1)

        # 处理并列值：取平均秩
        sorted_arr = arr[order]
        i = 0
        while i < len(arr):
            j = i
            while j + 1 < len(arr) and sorted_arr[j + 1] == sorted_arr[i]:
                j += 1
            if j > i:
                avg = (i + j) / 2 + 1  # 0-indexed → 1-indexed 平均秩
                ranks[order[i:j + 1]] = avg
            i = j + 1
        return ranks

    def get_adaptive_weights(self, db: Session, lookback_days: int = 30) -> Optional[Dict[str, float]]:
        """基于最近 N 日的平均 |IC| 归一化得到自适应权重

        无足够 IC 数据时返回 None（调用方退回固定权重）。
        """
        start_date = date.today() - timedelta(days=lookback_days)

        dates_rows = db.query(
            FactorPerformance.trade_date,
            FactorPerformance.factor_name,
            FactorPerformance.factor_value,
            FactorPerformance.forward_return_5d,
        ).filter(
            FactorPerformance.trade_date >= start_date,
            FactorPerformance.forward_return_5d != None,
        ).all()

        ic_by_date: Dict[date, Dict[str, float]] = defaultdict(dict)
        date_groups: Dict[date, Dict[str, List[tuple]]] = defaultdict(lambda: defaultdict(list))
        for d, fname, fval, fret in dates_rows:
            date_groups[d][fname].append((fval, fret))

        for d, factor_map in date_groups.items():
            for fname, pairs in factor_map.items():
                if len(pairs) < 10:
                    continue
                values = np.array([p[0] for p in pairs], dtype=float)
                returns = np.array([p[1] for p in pairs], dtype=float)
                valid = ~(np.isnan(values) | np.isnan(returns))
                if valid.sum() < 10:
                    continue
                corr = np.corrcoef(
                    self._rankdata(values[valid]),
                    self._rankdata(returns[valid]),
                )[0, 1]
                if not np.isnan(corr):
                    ic_by_date[d][fname] = corr

        # 平均 |IC|
        factor_ics: Dict[str, List[float]] = defaultdict(list)
        for d, fmap in ic_by_date.items():
            for fname, ic in fmap.items():
                factor_ics[fname].append(abs(ic))

        if not factor_ics:
            return None

        avg_ic: Dict[str, float] = {}
        for fname, ics in factor_ics.items():
            avg_ic[fname] = float(np.mean(ics))

        if not avg_ic or sum(avg_ic.values()) == 0:
            return None

        # 归一化为权重
        total = sum(avg_ic.values())
        weights = {fname: round(ic / total, 4) for fname, ic in avg_ic.items()}

        # 确保所有因子都有权重
        for f in FACTORS:
            if f not in weights:
                weights[f] = DEFAULT_WEIGHTS[f]

        # 重新归一化
        total = sum(weights.values())
        weights = {k: round(v / total, 4) for k, v in weights.items()}

        return weights

    def get_ic_history(self, db: Session, days: int = 30) -> List[Dict]:
        """获取近期每日IC历史（用于前端展示）"""
        start_date = date.today() - timedelta(days=days)

        rows = db.query(
            FactorPerformance.trade_date,
            FactorPerformance.factor_name,
            FactorPerformance.factor_value,
            FactorPerformance.forward_return_5d,
        ).filter(
            FactorPerformance.trade_date >= start_date,
            FactorPerformance.forward_return_5d != None,
        ).all()

        date_groups: Dict[date, Dict[str, List[tuple]]] = defaultdict(lambda: defaultdict(list))
        for d, fname, fval, fret in rows:
            date_groups[d][fname].append((fval, fret))

        history = []
        for d in sorted(date_groups.keys()):
            ic_map = {}
            for fname, pairs in date_groups[d].items():
                if len(pairs) < 10:
                    continue
                values = np.array([p[0] for p in pairs], dtype=float)
                returns = np.array([p[1] for p in pairs], dtype=float)
                valid = ~(np.isnan(values) | np.isnan(returns))
                if valid.sum() < 10:
                    continue
                corr = np.corrcoef(
                    self._rankdata(values[valid]),
                    self._rankdata(returns[valid]),
                )[0, 1]
                if not np.isnan(corr):
                    ic_map[fname] = round(float(corr), 4)
            if ic_map:
                history.append({"trade_date": d.isoformat(), "ic": ic_map})

        return history


_service: FactorPerformanceService | None = None


def get_factor_performance_service() -> FactorPerformanceService:
    global _service
    if _service is None:
        _service = FactorPerformanceService()
    return _service
