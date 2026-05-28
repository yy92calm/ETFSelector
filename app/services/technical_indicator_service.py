"""技术指标计算服务"""

import logging
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.etf import ETFQuotation

logger = logging.getLogger(__name__)


class TechnicalIndicatorService:
    """技术指标计算服务 - 提供多种技术指标计算"""
    
    INDICATOR_CONFIG = {
        "ma_periods": [5, 10, 20, 60],
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "rsi_period": 14,
        "bollinger_period": 20,
        "bollinger_std": 2,
    }
    
    def calculate_all_indicators(self, etf_code: str, db: Session, days: int = 60) -> Dict:
        """计算所有技术指标"""
        quotations = db.query(ETFQuotation).filter(
            ETFQuotation.etf_code == etf_code
        ).order_by(ETFQuotation.trade_date.desc()).limit(days).all()
        
        if len(quotations) < 20:
            return {"error": "数据不足"}
        
        quotations.reverse()
        prices = [q.close_price for q in quotations]
        volumes = [q.volume for q in quotations]
        dates = [q.trade_date for q in quotations]
        
        return {
            "etf_code": etf_code,
            "latest_date": dates[-1].isoformat() if dates else None,
            "ma": self._calculate_ma(prices),
            "macd": self._calculate_macd(prices),
            "rsi": self._calculate_rsi(prices),
            "bollinger": self._calculate_bollinger(prices),
            "volume_analysis": self._analyze_volume(volumes),
            "price_momentum": self._calculate_momentum(prices),
            "trend_signal": self._generate_trend_signal(prices),
        }
    
    def _calculate_ma(self, prices: List[float]) -> Dict:
        """计算移动平均线"""
        result = {}
        for period in self.INDICATOR_CONFIG["ma_periods"]:
            if len(prices) >= period:
                ma = sum(prices[-period:]) / period
                current_price = prices[-1]
                result[f"ma{period}"] = {
                    "value": round(ma, 4),
                    "price_position": "above" if current_price > ma else "below",
                    "distance_pct": round((current_price - ma) / ma * 100, 2),
                }
        return result
    
    def _calculate_macd(self, prices: List[float]) -> Dict:
        """计算MACD指标"""
        fast = self.INDICATOR_CONFIG["macd_fast"]
        slow = self.INDICATOR_CONFIG["macd_slow"]
        signal = self.INDICATOR_CONFIG["macd_signal"]
        
        if len(prices) < slow + signal:
            return {}
        
        ema_fast = self._calculate_ema(prices, fast)
        ema_slow = self._calculate_ema(prices, slow)
        
        if not ema_fast or not ema_slow:
            return {}
        
        valid_ema_fast = [f for f in ema_fast if f is not None]
        valid_ema_slow = [s for s in ema_slow if s is not None]
        
        if len(valid_ema_fast) < len(valid_ema_slow):
            valid_ema_fast = valid_ema_fast[-len(valid_ema_slow):]
        
        macd_line = [f - s for f, s in zip(valid_ema_fast, valid_ema_slow)]
        
        if not macd_line:
            return {}
        
        signal_line = self._calculate_ema_simple(macd_line, signal)
        histogram = [m - s for m, s in zip(macd_line[-len(signal_line):], signal_line)]
        
        latest_macd = macd_line[-1] if macd_line else 0
        latest_signal = signal_line[-1] if signal_line else 0
        latest_hist = histogram[-1] if histogram else 0
        
        return {
            "macd": round(latest_macd, 4),
            "signal": round(latest_signal, 4),
            "histogram": round(latest_hist, 4),
            "trend": "bullish" if latest_macd > latest_signal else "bearish",
            "strength": "strong" if (len(histogram) > 1 and abs(latest_hist) > abs(histogram[-2])) else "weak" if len(histogram) > 1 else "neutral",
        }
    
    def _calculate_ema_simple(self, data: List[float], period: int) -> List[float]:
        """计算EMA（简化版，不返回None）"""
        if len(data) < period:
            return []
        
        multiplier = 2 / (period + 1)
        ema = [sum(data[:period]) / period]
        
        for val in data[period:]:
            ema.append((val - ema[-1]) * multiplier + ema[-1])
        
        return ema
    
    def _calculate_rsi(self, prices: List[float]) -> Dict:
        """计算RSI指标"""
        period = self.INDICATOR_CONFIG["rsi_period"]
        
        if len(prices) < period + 1:
            return {}
        
        changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        
        gains = [c if c > 0 else 0 for c in changes[-period:]]
        losses = [-c if c < 0 else 0 for c in changes[-period:]]
        
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        signal = "neutral"
        if rsi > 70:
            signal = "overbought"
        elif rsi < 30:
            signal = "oversold"
        
        return {
            "value": round(rsi, 2),
            "signal": signal,
            "strength": "strong" if rsi > 80 or rsi < 20 else "moderate",
        }
    
    def _calculate_bollinger(self, prices: List[float]) -> Dict:
        """计算布林带"""
        period = self.INDICATOR_CONFIG["bollinger_period"]
        std_mult = self.INDICATOR_CONFIG["bollinger_std"]
        
        if len(prices) < period:
            return {}
        
        recent_prices = prices[-period:]
        ma = sum(recent_prices) / period
        variance = sum((p - ma) ** 2 for p in recent_prices) / period
        std = variance ** 0.5
        
        upper = ma + std_mult * std
        lower = ma - std_mult * std
        current = prices[-1]
        
        bandwidth = (upper - lower) / ma * 100
        position = (current - lower) / (upper - lower) * 100
        
        return {
            "upper": round(upper, 4),
            "middle": round(ma, 4),
            "lower": round(lower, 4),
            "bandwidth": round(bandwidth, 2),
            "position_pct": round(position, 2),
            "signal": "upper" if position > 80 else "lower" if position < 20 else "middle",
        }
    
    def _analyze_volume(self, volumes: List[int]) -> Dict:
        """分析成交量"""
        if len(volumes) < 5:
            return {}
        
        avg_volume = sum(volumes[:-1]) / (len(volumes) - 1)
        current_volume = volumes[-1]
        
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
        
        trend = "normal"
        if volume_ratio > 2.0:
            trend = "huge_increase"
        elif volume_ratio > 1.5:
            trend = "increase"
        elif volume_ratio < 0.5:
            trend = "decrease"
        
        return {
            "current": current_volume,
            "avg": round(avg_volume, 0),
            "ratio": round(volume_ratio, 2),
            "trend": trend,
        }
    
    def _calculate_momentum(self, prices: List[float]) -> Dict:
        """计算价格动量"""
        if len(prices) < 10:
            return {}
        
        changes = {}
        periods = [1, 3, 5, 10]
        
        for p in periods:
            if len(prices) > p:
                change = (prices[-1] - prices[-p-1]) / prices[-p-1] * 100
                changes[f"{p}d"] = round(change, 2)
        
        momentum_score = 0
        if changes.get("5d", 0) > 2:
            momentum_score = 2
        elif changes.get("5d", 0) > 0:
            momentum_score = 1
        elif changes.get("5d", 0) < -2:
            momentum_score = -2
        elif changes.get("5d", 0) < 0:
            momentum_score = -1
        
        return {
            "changes": changes,
            "momentum_score": momentum_score,
            "direction": "up" if momentum_score > 0 else "down" if momentum_score < 0 else "neutral",
        }
    
    def _generate_trend_signal(self, prices: List[float]) -> Dict:
        """生成综合趋势信号"""
        if len(prices) < 20:
            return {}
        
        ma5 = sum(prices[-5:]) / 5
        ma10 = sum(prices[-10:]) / 10
        ma20 = sum(prices[-20:]) / 20
        current = prices[-1]
        
        bullish_signals = 0
        bearish_signals = 0
        
        if current > ma5:
            bullish_signals += 1
        else:
            bearish_signals += 1
        
        if ma5 > ma10:
            bullish_signals += 1
        else:
            bearish_signals += 1
        
        if ma10 > ma20:
            bullish_signals += 1
        else:
            bearish_signals += 1
        
        if current > ma20:
            bullish_signals += 1
        else:
            bearish_signals += 1
        
        if bullish_signals >= 3:
            trend = "strong_bullish"
        elif bullish_signals > bearish_signals:
            trend = "bullish"
        elif bearish_signals >= 3:
            trend = "strong_bearish"
        elif bearish_signals > bullish_signals:
            trend = "bearish"
        else:
            trend = "neutral"
        
        return {
            "trend": trend,
            "bullish_signals": bullish_signals,
            "bearish_signals": bearish_signals,
            "confidence": round(max(bullish_signals, bearish_signals) / 4 * 100, 0),
        }
    
    def _calculate_ema(self, data: List[float], period: int) -> List[float]:
        """计算EMA"""
        if len(data) < period:
            return []
        
        multiplier = 2 / (period + 1)
        ema = [sum(data[:period]) / period]
        
        for price in data[period:]:
            ema.append((price - ema[-1]) * multiplier + ema[-1])
        
        return [None] * (period - 1) + ema
    
    def batch_calculate_indicators(self, etf_codes: List[str], db: Session) -> Dict[str, Dict]:
        """批量计算ETF指标"""
        results = {}
        for code in etf_codes:
            try:
                results[code] = self.calculate_all_indicators(code, db)
            except Exception as e:
                logger.error(f"计算{code}指标失败: {e}")
                results[code] = {"error": str(e)}
        
        return results