"""全市场ETF量化指标扫描服务（纯计算，无LLM）"""

import logging
from datetime import date
from typing import Dict, List

import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.etf import ETFBasic, ETFQuotation, ETFDailyIndicator
from app.models.factor_performance import FactorPerformance

logger = logging.getLogger(__name__)

WEIGHTS = {
    "momentum": 0.35,
    "trend": 0.20,
    "volume": 0.15,
    "volatility": 0.15,
    "capital_flow": 0.15,
}

MIN_AMOUNT_5D = 5_000_000
MIN_HISTORY_DAYS = 25


class MarketScannerService:
    """每个工作日对全量ETF计算量化指标并存库"""

    def scan_all(self, scan_date: date, db: Session) -> Dict:
        from sqlalchemy import func as sa_func
        actual_date = db.query(sa_func.max(ETFQuotation.trade_date)).scalar()
        if not actual_date:
            return {"status": "no_data", "count": 0}
        scan_date = actual_date

        existing = db.query(ETFDailyIndicator.etf_code).filter(
            ETFDailyIndicator.trade_date == scan_date
        ).count()
        if existing > 0:
            logger.info(f"[Scanner] {scan_date} 已有{existing}条指标，跳过")
            return {"status": "skipped", "count": existing}

        all_etfs = db.query(ETFBasic.etf_code).all()
        codes = [r[0] for r in all_etfs]
        logger.info(f"[Scanner] 开始扫描 {len(codes)} 只ETF ({scan_date})")

        results = []
        for code in codes:
            indicator = self._compute_indicator(code, scan_date, db)
            if indicator:
                results.append(indicator)

        if not results:
            return {"status": "no_data", "count": 0}

        scores = [r["composite_score"] for r in results]
        sorted_indices = np.argsort(scores)[::-1]
        for rank, idx in enumerate(sorted_indices, 1):
            results[idx]["rank_in_market"] = rank

        for r in results:
            db.add(ETFDailyIndicator(
                etf_code=r["etf_code"],
                trade_date=scan_date,
                momentum_5d=r["momentum_5d"],
                momentum_20d=r["momentum_20d"],
                momentum_score=r["momentum_score"],
                trend_strength=r["trend_strength"],
                ma5=r["ma5"],
                ma10=r["ma10"],
                ma20=r["ma20"],
                vol_ratio=r["vol_ratio"],
                volatility_20d=r["volatility_20d"],
                obv_slope=r["obv_slope"],
                amount_avg_5d=r["amount_avg_5d"],
                composite_score=r["composite_score"],
                rank_in_market=r["rank_in_market"],
            ))

            # 记录因子表现（供IC计算和自适应权重）
            for factor_name, score in (r.get("factor_scores") or {}).items():
                db.add(FactorPerformance(
                    etf_code=r["etf_code"],
                    trade_date=scan_date,
                    factor_name=factor_name,
                    factor_value=score,
                ))

        db.commit()
        logger.info(f"[Scanner] 完成: {len(results)}只ETF指标已存库")
        return {"status": "ok", "count": len(results)}

    def get_top_n(self, scan_date: date, n: int, db: Session) -> List[Dict]:
        rows = db.query(ETFDailyIndicator).filter(
            ETFDailyIndicator.trade_date == scan_date
        ).order_by(ETFDailyIndicator.composite_score.desc()).limit(n).all()

        name_map = {e.etf_code: e.etf_name for e in db.query(ETFBasic).all()}
        return [{
            "etf_code": r.etf_code,
            "etf_name": name_map.get(r.etf_code, ""),
            "composite_score": r.composite_score,
            "rank": r.rank_in_market,
            "momentum_5d": r.momentum_5d,
            "momentum_20d": r.momentum_20d,
            "trend_strength": r.trend_strength,
            "volatility_20d": r.volatility_20d,
            "vol_ratio": r.vol_ratio,
        } for r in rows]

    def get_holding_scores(self, scan_date: date, codes: List[str], db: Session) -> List[Dict]:
        rows = db.query(ETFDailyIndicator).filter(
            ETFDailyIndicator.trade_date == scan_date,
            ETFDailyIndicator.etf_code.in_(codes),
        ).all()

        name_map = {e.etf_code: e.etf_name for e in db.query(ETFBasic).all()}
        return [{
            "etf_code": r.etf_code,
            "etf_name": name_map.get(r.etf_code, ""),
            "composite_score": r.composite_score,
            "rank": r.rank_in_market,
            "momentum_5d": r.momentum_5d,
            "momentum_20d": r.momentum_20d,
            "trend_strength": r.trend_strength,
        } for r in rows]

    def _compute_indicator(self, etf_code: str, scan_date: date, db: Session) -> Dict | None:
        quotes = db.query(ETFQuotation).filter(
            ETFQuotation.etf_code == etf_code,
            ETFQuotation.trade_date <= scan_date,
        ).order_by(ETFQuotation.trade_date.desc()).limit(30).all()

        if len(quotes) < MIN_HISTORY_DAYS:
            return None

        quotes.reverse()
        prices = [q.close_price for q in quotes]
        volumes = [q.volume for q in quotes]
        amounts = [q.amount for q in quotes]

        if prices[-1] <= 0:
            return None

        amount_avg_5d = float(np.mean(amounts[-5:]))
        if amount_avg_5d < MIN_AMOUNT_5D:
            return None

        momentum_5d = (prices[-1] / prices[-6] - 1) * 100 if len(prices) >= 6 else 0
        momentum_20d = (prices[-1] / prices[-21] - 1) * 100 if len(prices) >= 21 else 0
        momentum_score = momentum_5d * 0.6 + momentum_20d * 0.4

        ma5 = float(np.mean(prices[-5:]))
        ma10 = float(np.mean(prices[-10:]))
        ma20 = float(np.mean(prices[-20:]))
        trend_strength = sum([
            prices[-1] > ma5,
            ma5 > ma10,
            ma10 > ma20,
        ])

        vol_5 = float(np.mean(volumes[-5:]))
        vol_20 = float(np.mean(volumes[-20:]))
        vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0

        daily_rets = np.diff(prices[-21:]) / np.array(prices[-21:-1])
        volatility_20d = float(np.std(daily_rets) * np.sqrt(252) * 100)

        obv_slope = self._obv_slope(prices[-6:], volumes[-6:])

        vol_score = self._volume_score(vol_ratio, momentum_5d)
        volatility_score = self._volatility_score(volatility_20d)
        flow_score = self._flow_score(obv_slope, amount_avg_5d)

        # 各因子得分（供IC跟踪和动态权重使用）
        factor_scores = {
            "momentum": self._normalize_momentum(momentum_score),
            "trend": trend_strength / 3.0 * 100,
            "volume": vol_score,
            "volatility": volatility_score,
            "capital_flow": flow_score,
        }

        # 动态权重：优先使用IC自适应权重，无数据时退回固定权重
        weights = WEIGHTS
        try:
            from app.services.factor_performance_service import get_factor_performance_service
            adaptive = get_factor_performance_service().get_adaptive_weights(db)
            if adaptive:
                weights = adaptive
        except Exception:
            pass

        composite = (
            weights["momentum"] * factor_scores["momentum"]
            + weights["trend"] * factor_scores["trend"]
            + weights["volume"] * factor_scores["volume"]
            + weights["volatility"] * factor_scores["volatility"]
            + weights["capital_flow"] * factor_scores["capital_flow"]
        )

        return {
            "etf_code": etf_code,
            "momentum_5d": round(momentum_5d, 2),
            "momentum_20d": round(momentum_20d, 2),
            "momentum_score": round(momentum_score, 2),
            "trend_strength": trend_strength,
            "ma5": round(ma5, 4),
            "ma10": round(ma10, 4),
            "ma20": round(ma20, 4),
            "vol_ratio": round(vol_ratio, 3),
            "volatility_20d": round(volatility_20d, 2),
            "obv_slope": round(obv_slope, 4),
            "amount_avg_5d": round(amount_avg_5d, 0),
            "composite_score": round(composite, 2),
            "rank_in_market": 0,
            "factor_scores": factor_scores,
        }

    def _obv_slope(self, prices: List[float], volumes: List[float]) -> float:
        obv = [0.0]
        for i in range(1, len(prices)):
            if prices[i] > prices[i-1]:
                obv.append(obv[-1] + volumes[i])
            elif prices[i] < prices[i-1]:
                obv.append(obv[-1] - volumes[i])
            else:
                obv.append(obv[-1])
        if len(obv) < 2:
            return 0.0
        return (obv[-1] - obv[0]) / max(abs(obv[0]), 1)

    def _normalize_momentum(self, score: float) -> float:
        return max(0, min(100, (score + 20) / 40 * 100))

    def _volume_score(self, vol_ratio: float, momentum_5d: float) -> float:
        if momentum_5d > 0 and vol_ratio > 1.2:
            return min(100, 60 + (vol_ratio - 1.2) * 80)
        if momentum_5d > 0 and vol_ratio < 0.8:
            return 30
        if momentum_5d < 0 and vol_ratio > 1.5:
            return 10
        return 50

    def _volatility_score(self, vol: float) -> float:
        if vol < 15:
            return 80
        if vol < 25:
            return 70
        if vol < 35:
            return 50
        if vol < 45:
            return 30
        return 10

    def _flow_score(self, obv_slope: float, amount: float) -> float:
        score = 50
        if obv_slope > 0.1:
            score += 30
        elif obv_slope > 0:
            score += 15
        elif obv_slope < -0.1:
            score -= 30
        elif obv_slope < 0:
            score -= 15
        if amount > 50_000_000:
            score += 20
        elif amount > 20_000_000:
            score += 10
        return max(0, min(100, score))


_service: MarketScannerService | None = None


def get_market_scanner_service() -> MarketScannerService:
    global _service
    if _service is None:
        _service = MarketScannerService()
    return _service
