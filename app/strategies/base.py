"""
ETF配置组合策略基类
基于再平衡逻辑而非技术指标择时
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import date, timedelta
from enum import Enum

import pandas as pd


class RebalanceTrigger(Enum):
    """再平衡触发类型"""
    TIME_BASED = "time_based"       # 时间触发（季度末/月末）
    THRESHOLD_BASED = "threshold"   # 偏离阈值触发
    INITIAL = "initial"             # 初始买入
    ALLOCATION_CHANGE = "allocation_change"  # 配置变更（调仓，含遗留持仓清理）


@dataclass
class RebalanceSignal:
    """再平衡信号"""
    trigger_type: RebalanceTrigger
    trade_date: date
    adjustments: List[Dict]  # 调整列表 [{"etf_code": "xxx", "action": "buy/sell", "amount": 10000}]
    reason: str = ""


@dataclass
class PortfolioContext:
    """组合运行上下文"""
    current_date: date
    total_asset: float
    holdings: Dict[str, int] = field(default_factory=dict)  # etf_code -> quantity
    current_prices: Dict[str, float] = field(default_factory=dict)  # etf_code -> current_price
    allocation_config: Dict[str, float] = field(default_factory=dict)  # 目标配置比例
    rebalance_threshold: float = 0.05
    history_dates: List[date] = field(default_factory=list)  # 历史交易日列表


def compute_adjustment(
    action: str,
    amount: float,
    price: float,
    current_qty: int,
    cash: float,
) -> Optional[Dict]:
    """计算单笔再平衡调整的买卖结果（100股整数倍取整）

    回测与实盘共用，确保两边行为一致。

    Returns:
        {"quantity", "actual_amount", "cash_delta", "new_qty", "direction"} 或 None
    """
    if price <= 0:
        return None

    if action == "buy" and cash >= amount:
        quantity = int(amount / price / 100) * 100
        if quantity > 0:
            actual_amount = quantity * price
            return {
                "quantity": quantity,
                "actual_amount": actual_amount,
                "cash_delta": -actual_amount,
                "new_qty": current_qty + quantity,
                "direction": "buy",
            }
    elif action == "sell" and current_qty > 0:
        sell_qty = min(int(amount / price / 100) * 100, current_qty)
        if sell_qty > 0:
            actual_amount = sell_qty * price
            new_qty = current_qty - sell_qty
            if new_qty <= 0:
                new_qty = 0
            return {
                "quantity": sell_qty,
                "actual_amount": actual_amount,
                "cash_delta": actual_amount,
                "new_qty": new_qty,
                "direction": "sell",
            }
    return None


class AllocationStrategy(ABC):
    """配置组合策略基类"""
    
    name: str = "base"
    description: str = ""
    allocation_config: Dict[str, float] = {}
    rebalance_freq: str = "quarterly"
    rebalance_threshold: float = 0.05
    
    def __init__(self, allocation_config: Optional[Dict] = None):
        if allocation_config:
            self.allocation_config = allocation_config
        
        # 验证配置比例总和为1
        total = sum(self.allocation_config.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"配置比例总和应为1.0，当前为 {total}")
    
    @abstractmethod
    def check_rebalance(self, ctx: PortfolioContext) -> bool:
        """
        判断是否需要再平衡
        返回True表示需要执行再平衡
        """
        ...
    
    @abstractmethod
    def generate_rebalance_signals(self, ctx: PortfolioContext) -> List[RebalanceSignal]:
        """
        生成再平衡信号
        返回需要执行的调整操作
        """
        ...
    
    def calculate_current_allocation(self, ctx: PortfolioContext) -> Dict[str, float]:
        """
        计算当前持仓的实际配置比例
        """
        if ctx.total_asset <= 0:
            return {}
        
        current_allocation = {}
        for etf_code, quantity in ctx.holdings.items():
            if quantity <= 0:
                continue
            
            price = ctx.current_prices.get(etf_code, 0)
            if price <= 0:
                continue
            
            market_value = quantity * price
            allocation_ratio = market_value / ctx.total_asset
            current_allocation[etf_code] = allocation_ratio
        
        return current_allocation
    
    def calculate_deviation(self, current_allocation: Dict[str, float]) -> Dict[str, float]:
        """
        计算配置偏离度
        """
        deviation = {}
        for etf_code, target_ratio in self.allocation_config.items():
            current_ratio = current_allocation.get(etf_code, 0)
            deviation[etf_code] = abs(current_ratio - target_ratio)
        
        return deviation
    
    def should_trigger_threshold_rebalance(self, ctx: PortfolioContext) -> bool:
        """
        判断是否触发偏离阈值再平衡
        """
        current_allocation = self.calculate_current_allocation(ctx)
        deviation = self.calculate_deviation(current_allocation)
        
        # 任意ETF偏离超过阈值则触发
        max_deviation = max(deviation.values()) if deviation else 0
        return max_deviation >= self.rebalance_threshold
    
    def should_trigger_time_rebalance(self, ctx: PortfolioContext) -> bool:
        """
        判断是否触发时间再平衡（季度末/月末/周末/年末）

        回测模式：history_dates 包含 current_date 之后的交易日，用 next_date 判断换月。
        实盘模式：current_date 是 history_dates 最后一天（当日执行），用日历判断期末。
        """
        current_date = ctx.current_date
        history_dates = ctx.history_dates

        if self.rebalance_freq == "none":
            return False

        if not history_dates:
            return False

        if current_date not in history_dates:
            return False

        current_idx = history_dates.index(current_date)

        if self.rebalance_freq == "daily":
            return current_idx > 0

        # 实盘模式：current_date 是已知最后交易日，用日历判断是否期末
        if current_idx == len(history_dates) - 1:
            return self._is_period_end_by_calendar(current_date)

        next_date = history_dates[current_idx + 1]

        if self.rebalance_freq == "quarterly":
            quarter_end_months = [3, 6, 9, 12]
            is_quarter_end = current_date.month in quarter_end_months and next_date.month != current_date.month
            return is_quarter_end

        elif self.rebalance_freq == "monthly":
            is_month_end = next_date.month != current_date.month
            return is_month_end

        elif self.rebalance_freq == "weekly":
            is_week_end = next_date.weekday() == 0 and current_date.weekday() != 0
            return is_week_end

        elif self.rebalance_freq == "yearly":
            is_year_end = next_date.year != current_date.year
            return is_year_end

        return False

    def _is_period_end_by_calendar(self, current_date: date) -> bool:
        """
        日历判断 current_date 是否为频率期末的最后一个工作日（实盘模式）

        通过"下一个工作日是否跨期"判断，无需未来行情数据。
        """
        next_workday = current_date + timedelta(days=1)
        while next_workday.weekday() >= 5:
            next_workday += timedelta(days=1)

        if self.rebalance_freq == "quarterly":
            if current_date.month not in [3, 6, 9, 12]:
                return False
            return next_workday.month != current_date.month

        elif self.rebalance_freq == "monthly":
            return next_workday.month != current_date.month

        elif self.rebalance_freq == "weekly":
            return current_date.weekday() == 4

        elif self.rebalance_freq == "yearly":
            return next_workday.year != current_date.year

        return False
    
    def get_info(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "allocation_config": self.allocation_config,
            "rebalance_freq": self.rebalance_freq,
            "rebalance_threshold": self.rebalance_threshold,
        }
