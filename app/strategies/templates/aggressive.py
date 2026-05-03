"""
激进型配置模板
适合高风险偏好投资者，以成长型ETF为主
"""

from typing import Dict


class AggressiveAllocation:
    """激进型ETF配置"""
    
    name = "aggressive"
    description = "激进型配置：创业板(30%) + 纳斯达克(50%) + 债券(20%)，适合追求高成长的年轻投资者"
    
    # ETF配置比例
    allocation_config: Dict[str, float] = {
        "159915": 0.3,   # 创业板ETF 30%
        "513100": 0.5,   # 纳斯达克ETF 50%
        "511010": 0.2,   # 国债ETF 20%
    }
    
    # 再平衡参数
    rebalance_freq = "quarterly"  # 季度再平衡
    rebalance_threshold = 0.08    # 8%偏离触发（激进策略容忍更大偏离）
    
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