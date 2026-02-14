"""MACD策略"""

from typing import List
import pandas as pd
from app.strategies.base import BaseStrategy, Signal, StrategyContext


class MACDStrategy(BaseStrategy):
    """
    MACD策略
    MACD柱由负转正买入，由正转负卖出
    """

    name = "macd"
    description = "MACD策略：MACD柱由负转正买入，由正转负卖出"
    default_params = {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
    }

    def _calc_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        fast = self.params["fast_period"]
        slow = self.params["slow_period"]
        signal = self.params["signal_period"]

        df = df.copy()
        df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
        df["dif"] = df["ema_fast"] - df["ema_slow"]
        df["dea"] = df["dif"].ewm(span=signal, adjust=False).mean()
        df["macd_hist"] = 2 * (df["dif"] - df["dea"])
        return df

    def generate_signals(self, ctx: StrategyContext) -> List[Signal]:
        df = self._calc_macd(ctx.history)
        df = df.dropna()

        if len(df) < 2:
            return []

        prev = df.iloc[-2]
        curr = df.iloc[-1]
        signals = []

        # MACD柱由负转正 → 买入
        if prev["macd_hist"] <= 0 and curr["macd_hist"] > 0:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="buy",
                reason="MACD柱由负转正",
            ))

        # MACD柱由正转负 → 卖出
        if prev["macd_hist"] >= 0 and curr["macd_hist"] < 0:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="sell",
                reason="MACD柱由正转负",
            ))

        return signals
