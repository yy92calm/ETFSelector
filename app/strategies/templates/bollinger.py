"""布林带策略"""

from typing import List
import pandas as pd
from app.strategies.base import BaseStrategy, Signal, StrategyContext


class BollingerStrategy(BaseStrategy):
    """
    布林带策略
    价格触及下轨买入，触及上轨卖出
    """

    name = "bollinger"
    description = "布林带策略：价格触及下轨买入，触及上轨卖出"
    default_params = {
        "period": 20,
        "std_dev": 2.0,
    }

    def generate_signals(self, ctx: StrategyContext) -> List[Signal]:
        df = ctx.history.copy()
        period = self.params["period"]

        if len(df) < period + 1:
            return []

        df["ma"] = df["close"].rolling(window=period).mean()
        df["std"] = df["close"].rolling(window=period).std()
        df["upper"] = df["ma"] + self.params["std_dev"] * df["std"]
        df["lower"] = df["ma"] - self.params["std_dev"] * df["std"]
        df = df.dropna()

        if len(df) < 2:
            return []

        prev = df.iloc[-2]
        curr = df.iloc[-1]
        signals = []

        # 从下轨反弹 → 买入
        if prev["close"] <= prev["lower"] and curr["close"] > curr["lower"]:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="buy",
                reason=f"价格从布林下轨反弹",
            ))

        # 从上轨回落 → 卖出
        if prev["close"] >= prev["upper"] and curr["close"] < curr["upper"]:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="sell",
                reason=f"价格从布林上轨回落",
            ))

        return signals
