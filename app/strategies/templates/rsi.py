"""RSI策略"""

from typing import List
import pandas as pd
from app.strategies.base import BaseStrategy, Signal, StrategyContext


class RSIStrategy(BaseStrategy):
    """
    RSI相对强弱指标策略
    RSI低于超卖线买入，高于超买线卖出
    """

    name = "rsi"
    description = "RSI策略：RSI低于超卖线时买入，高于超买线时卖出"
    default_params = {
        "period": 14,
        "oversold": 30,
        "overbought": 70,
    }

    def _calc_rsi(self, series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        return 100 - (100 / (1 + rs))

    def generate_signals(self, ctx: StrategyContext) -> List[Signal]:
        df = ctx.history.copy()
        period = self.params["period"]

        if len(df) < period + 2:
            return []

        df["rsi"] = self._calc_rsi(df["close"], period)
        df = df.dropna()

        if len(df) < 2:
            return []

        prev = df.iloc[-2]
        curr = df.iloc[-1]
        signals = []

        # RSI从超卖区回升 → 买入
        if prev["rsi"] <= self.params["oversold"] and curr["rsi"] > self.params["oversold"]:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="buy",
                reason=f"RSI从超卖区({self.params['oversold']})回升至{curr['rsi']:.1f}",
            ))

        # RSI从超买区回落 → 卖出
        if prev["rsi"] >= self.params["overbought"] and curr["rsi"] < self.params["overbought"]:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="sell",
                reason=f"RSI从超买区({self.params['overbought']})回落至{curr['rsi']:.1f}",
            ))

        return signals
