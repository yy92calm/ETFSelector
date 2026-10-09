"""因子表现服务 - 计算IC（信息系数）、自适应权重"""

import bisect
import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

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

        indicators = db.query(ETFDailyIndicator).all()
        if not indicators:
            return 0

        # 按记录身份（代码+日期+因子）去重，只跳过真正已存在的那一条：
        # 逐个因子补齐时不能因为某日已有部分因子就跳过整日，否则缺失因子永远补不上
        existing = {(r[0], r[1], r[2]) for r in db.query(
            FactorPerformance.etf_code,
            FactorPerformance.trade_date,
            FactorPerformance.factor_name,
        ).filter(FactorPerformance.trade_date.in_(
            {i.trade_date for i in indicators}
        )).distinct().all()}

        from app.services.market_scanner_service import get_market_scanner_service
        scanner = get_market_scanner_service()

        added = 0
        for ind in indicators:
            scores = {
                "momentum": scanner._normalize_momentum(ind.momentum_score or 0),
                "trend": (ind.trend_strength or 0) / 3.0 * 100,
                "volume": scanner._volume_score(ind.vol_ratio or 1.0, ind.momentum_5d or 0),
                "volatility": scanner._volatility_score(ind.volatility_20d or 0),
                "capital_flow": scanner._flow_score(ind.obv_slope or 0, ind.amount_avg_5d or 0),
            }
            for fname, score in scores.items():
                key = (ind.etf_code, ind.trade_date, fname)
                if key in existing:
                    continue
                existing.add(key)
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

        # 逐条查「T 之后 5 日行情」会让回填产生与记录数同阶的 SQL 往返，
        # 这里一次性拉平整个日期区间的收盘价后在内存定位 T+5
        trade_dates_by_code, close_prices = self._load_close_prices(
            {p.etf_code for p in pending},
            min(p.trade_date for p in pending),
            db,
        )

        filled = 0
        for p in pending:
            dates = trade_dates_by_code.get(p.etf_code)
            if not dates:
                continue

            # bisect 定位 T 之后的第一个交易日，第5个即 T+5
            start = bisect.bisect_right(dates, p.trade_date)
            if start + 4 >= len(dates):
                continue

            base_price = close_prices.get((p.etf_code, p.trade_date))
            target_price = close_prices.get((p.etf_code, dates[start + 4]))
            if base_price and target_price and base_price > 0:
                p.forward_return_5d = round((target_price - base_price) / base_price * 100, 4)
                filled += 1

        if filled > 0:
            db.commit()
            logger.info(f"[FactorPerf] 回填 {filled} 条未来5日收益")
        return filled

    @staticmethod
    def _load_close_prices(codes: set, since_date: date,
                           db: Session) -> Tuple[Dict[str, List[date]], Dict[Tuple[str, date], float]]:
        """一次查询取回区间内收盘价

        返回 ({代码: 升序交易日列表}, {(代码, 日期): 收盘价})。
        收盘价为空的行情不参与交易日计数，避免把停牌日算进 T+5。
        """
        rows = db.query(ETFQuotation.etf_code, ETFQuotation.trade_date, ETFQuotation.close_price) \
            .filter(ETFQuotation.etf_code.in_(codes),
                    ETFQuotation.trade_date >= since_date,
                    ETFQuotation.close_price != None) \
            .order_by(ETFQuotation.trade_date.asc()) \
            .all()

        trade_dates_by_code: Dict[str, List[date]] = defaultdict(list)
        close_prices: Dict[Tuple[str, date], float] = {}
        for code, trade_date, close_price in rows:
            close_prices[(code, trade_date)] = close_price
            trade_dates_by_code[code].append(trade_date)

        return trade_dates_by_code, close_prices

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
