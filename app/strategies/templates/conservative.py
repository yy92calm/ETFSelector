"""
保守型配置模板
适合风险厌恶型投资者，以债券为主，少量股票和黄金
"""

from typing import Dict


class ConservativeAllocation:
    """保守型ETF配置"""
    
    name = "conservative"
    description = "保守型配置：债券为主(70%)，配合少量大盘股(20%)和黄金(10%)，适合稳健型投资者"
    
    # ETF配置比例
    allocation_config: Dict[str, float] = {
        "511010": 0.7,   # 国债ETF 70%
        "510050": 0.2,   # 上证50ETF 20%
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