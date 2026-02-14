"""双均线交叉策略"""

from typing import List
import pandas as pd
from app.strategies.base import BaseStrategy, Signal, StrategyContext


class MACrossStrategy(BaseStrategy):
    """
    双均线交叉策略
    短期均线上穿长期均线时买入，下穿时卖出
    """

    name = "ma_cross"
    description = "双均线交叉策略：短期均线上穿长期均线买入，下穿卖出"
    default_params = {
        "short_window": 5,
        "long_window": 20,
    }

    def generate_signals(self, ctx: StrategyContext) -> List[Signal]:
        df = ctx.history.copy()
        if len(df) < self.params["long_window"] + 1:
            return []

        short_w = self.params["short_window"]
        long_w = self.params["long_window"]

        df["ma_short"] = df["close"].rolling(window=short_w).mean()
        df["ma_long"] = df["close"].rolling(window=long_w).mean()
        df = df.dropna()

        if len(df) < 2:
            return []

        # 只看最近两根K线的交叉
        prev = df.iloc[-2]
        curr = df.iloc[-1]

        signals = []

        # 金叉买入
        if prev["ma_short"] <= prev["ma_long"] and curr["ma_short"] > curr["ma_long"]:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="buy",
                reason=f"MA{short_w}上穿MA{long_w}金叉",
            ))

        # 死叉卖出
        if prev["ma_short"] >= prev["ma_long"] and curr["ma_short"] < curr["ma_long"]:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="sell",
                reason=f"MA{short_w}下穿MA{long_w}死叉",
            ))

        return signals
