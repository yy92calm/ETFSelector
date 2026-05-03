"""
均衡型配置模板
适合中等风险偏好投资者，股债均衡配置
"""

from typing import Dict


class BalancedAllocation:
    """均衡型ETF配置"""
    
    name = "balanced"
    description = "均衡型配置：沪深300(50%) + 债券(40%) + 黄金(10%)，适合追求稳健增长的投资者"
    
    # ETF配置比例
    allocation_config: Dict[str, float] = {
        "510300": 0.5,   # 沪深300ETF 50%
        "511010": 0.4,   # 国债ETF 40%
        "518880": 0.1,   # 黄金ETF 10%
    }
    
    # 再平衡参数
    rebalance_freq = "quarterly"  # 季度再平衡
    rebalance_threshold = 0.05    # 5%偏离触发
    
    def get_allocation(self) -> Dict[str, float]:
        """返回配置比例"""
        return self.allocation_config
    
    def get_info(self) -> dict:
        """返回模板信息"""
        return {
            "name": self.name,
            "description": self.description,
            "allocation_config": self.allocation_config,
            "rebalance_freq": self.rebalance_freq,
            "rebalance_threshold": self.rebalance_threshold,
        }