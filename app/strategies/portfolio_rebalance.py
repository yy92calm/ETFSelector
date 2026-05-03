"""
ETF配置组合再平衡策略实现
"""

from typing import List, Dict
from datetime import date

from app.strategies.base import AllocationStrategy, PortfolioContext, RebalanceSignal, RebalanceTrigger


class PortfolioRebalanceStrategy(AllocationStrategy):
    """配置组合再平衡策略"""
    
    def __init__(self, allocation_config: Dict[str, float], rebalance_freq: str = "quarterly", 
                 rebalance_threshold: float = 0.05):
        super().__init__(allocation_config)
        self.name = "portfolio_rebalance"
        self.description = "基于配置比例的自动再平衡策略"
        self.rebalance_freq = rebalance_freq
        self.rebalance_threshold = rebalance_threshold
    
    def check_rebalance(self, ctx: PortfolioContext) -> bool:
        """
        判断是否需要再平衡
        触发条件：
        1. 持仓为空（初始买入）
        2. 时间触发（季度末/月末）
        3. 偏离触发（配置偏离超过阈值）
        """
        # 条件1：持仓为空，需要初始买入
        if not ctx.holdings or sum(ctx.holdings.values()) == 0:
            return True
        
        # 条件2：时间触发
        if self.should_trigger_time_rebalance(ctx):
            return True
        
        # 条件3：偏离触发
        if self.should_trigger_threshold_rebalance(ctx):
            return True
        
        return False
    
    def generate_rebalance_signals(self, ctx: PortfolioContext) -> List[RebalanceSignal]:
        """
        生成再平衡信号
        """
        signals = []
        
        # 判断触发类型
        trigger_type = RebalanceTrigger.INITIAL
        
        if ctx.holdings and sum(ctx.holdings.values()) > 0:
            # 已有持仓
            if self.should_trigger_time_rebalance(ctx):
                trigger_type = RebalanceTrigger.TIME_BASED
            elif self.should_trigger_threshold_rebalance(ctx):
                trigger_type = RebalanceTrigger.THRESHOLD_BASED
            else:
                return []  # 无需再平衡
        
        # 计算调整操作
        adjustments = self._calculate_adjustments(ctx, trigger_type)
        
        if adjustments:
            signal = RebalanceSignal(
                trigger_type=trigger_type,
                trade_date=ctx.current_date,
                adjustments=adjustments,
                reason=self._get_reason(trigger_type, ctx)
            )
            signals.append(signal)
        
        return signals
    
    def _calculate_adjustments(self, ctx: PortfolioContext, trigger_type: RebalanceTrigger) -> List[Dict]:
        """
        计算具体的调整操作
        """
        adjustments = []
        
        # 计算当前配置比例
        current_allocation = self.calculate_current_allocation(ctx)
        
        # 对每个ETF计算需要调整的数量
        for etf_code, target_ratio in self.allocation_config.items():
            current_ratio = current_allocation.get(etf_code, 0)
            
            target_amount = ctx.total_asset * target_ratio
            current_amount = ctx.total_asset * current_ratio
            
            price = ctx.current_prices.get(etf_code, 0)
            if price <= 0:
                continue
            
            # 计算需要调整的金额
            amount_diff = target_amount - current_amount
            
            if abs(amount_diff) > 100:  # 最小调整金额100元
                action = "buy" if amount_diff > 0 else "sell"
                adjustments.append({
                    "etf_code": etf_code,
                    "action": action,
                    "amount": abs(amount_diff),
                    "target_ratio": target_ratio,
                    "current_ratio": current_ratio,
                    "deviation": abs(current_ratio - target_ratio)
                })
        
        return adjustments
    
    def _get_reason(self, trigger_type: RebalanceTrigger, ctx: PortfolioContext) -> str:
        """生成再平衡原因说明"""
        if trigger_type == RebalanceTrigger.INITIAL:
            return "初始买入：按配置比例建立持仓"
        
        elif trigger_type == RebalanceTrigger.TIME_BASED:
            freq_text = {
                "quarterly": "季度末",
                "monthly": "月末",
                "yearly": "年末"
            }.get(self.rebalance_freq, self.rebalance_freq)
            return f"时间触发：{freq_text}定期再平衡"
        
        elif trigger_type == RebalanceTrigger.THRESHOLD_BASED:
            current_allocation = self.calculate_current_allocation(ctx)
            deviation = self.calculate_deviation(current_allocation)
            
            max_deviation_etf = max(deviation.items(), key=lambda x: x[1])
            return f"偏离触发：{max_deviation_etf[0]}偏离目标配置 {max_deviation_etf[1]:.1%}"
        
        return "再平衡"