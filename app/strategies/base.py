"""
策略基类
所有策略（模板策略和AI生成策略）都需要实现这个接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import date

import pandas as pd


@dataclass
class Signal:
    """交易信号"""
    trade_date: date
    etf_code: str
    direction: str          # "buy" | "sell"
    strength: float = 1.0   # 信号强度 0~1，可用于仓位管理
    reason: str = ""


@dataclass
class StrategyContext:
    """策略运行上下文"""
    etf_code: str
    history: pd.DataFrame       # 历史行情 DataFrame，列: date, open, close, high, low, volume, amount, change_pct
    current_date: date
    holdings: Dict[str, int] = field(default_factory=dict)  # etf_code -> quantity
    cash: float = 0.0
    params: Dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """策略基类"""

    name: str = "base"
    description: str = ""
    default_params: dict = {}

    def __init__(self, params: Optional[dict] = None):
        self.params = {**self.default_params}
        if params:
            self.params.update(params)

    @abstractmethod
    def generate_signals(self, ctx: StrategyContext) -> List[Signal]:
        """
        根据上下文生成交易信号
        返回一组 Signal，引擎会根据信号执行买卖
        """
        ...

    def get_info(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "params": self.params,
        }
