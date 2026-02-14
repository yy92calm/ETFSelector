"""动量策略"""

from typing import List
import pandas as pd
from app.strategies.base import BaseStrategy, Signal, StrategyContext


class MomentumStrategy(BaseStrategy):
    """
    动量策略
    过去N日涨幅超过阈值买入，跌幅超过阈值卖出
    """

    name = "momentum"
    description = "动量策略：过去N日涨幅超过阈值时买入，跌幅超过阈值时卖出"
    default_params = {
        "lookback": 20,
        "buy_threshold": 5.0,   # 涨幅阈值 %
        "sell_threshold": -5.0,  # 跌幅阈值 %
    }

    def generate_signals(self, ctx: StrategyContext) -> List[Signal]:
        df = ctx.history.copy()
        lookback = self.params["lookback"]

        if len(df) < lookback + 1:
            return []

        # 计算N日动量（百分比）
        current_close = df.iloc[-1]["close"]
        past_close = df.iloc[-(lookback + 1)]["close"]
        momentum = (current_close - past_close) / past_close * 100

        signals = []

        if momentum >= self.params["buy_threshold"]:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="buy",
                strength=min(momentum / 10, 1.0),
                reason=f"{lookback}日动量 {momentum:.2f}% > {self.params['buy_threshold']}%",
            ))

        if momentum <= self.params["sell_threshold"]:
            signals.append(Signal(
                trade_date=ctx.current_date,
                etf_code=ctx.etf_code,
                direction="sell",
                strength=min(abs(momentum) / 10, 1.0),
                reason=f"{lookback}日动量 {momentum:.2f}% < {self.params['sell_threshold']}%",
            ))

        return signals
