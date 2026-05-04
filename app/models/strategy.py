"""策略模型 - ETF配置组合"""

from sqlalchemy import Column, String, Integer, Text, DateTime, JSON, Float
from datetime import datetime
from app.db.database import Base


class Strategy(Base):
    """ETF配置组合策略"""
    __tablename__ = "strategy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="策略名称")
    description = Column(Text, nullable=True, comment="策略描述（自然语言）")
    strategy_type = Column(String(30), nullable=False, default="template", comment="template / ai_generated")
    
    # 配置组合核心字段
    allocation_config = Column(JSON, nullable=False, comment="ETF配置比例，如 {'510300': 0.5, '511010': 0.4, '518880': 0.1}")
    rebalance_freq = Column(String(20), nullable=False, default="monthly", comment="再平衡检查频率：daily/weekly/monthly/quarterly/yearly/none")
    rebalance_threshold = Column(Float, nullable=False, default=0.05, comment="偏离阈值触发再平衡（默认5%）")
    
    # AI生成相关（保留）
    code = Column(Text, nullable=True, comment="策略代码（AI生成时使用，暂保留但主要用allocation_config）")
    
    # 其他字段
    initial_capital = Column(Integer, nullable=False, default=100000, comment="初始资金")
    status = Column(String(20), nullable=False, default="active", comment="active / paused / archived")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 旧字段保留但不使用（兼容性）
    template_name = Column(String(50), nullable=True, comment="模板名称（旧版，已废弃）")
    params = Column(JSON, nullable=True, comment="策略参数（旧版，已废弃）")
    etf_codes = Column(JSON, nullable=True, comment="关联ETF代码列表（旧版，已废弃）")

    def __repr__(self):
        return f"<Strategy {self.id}: {self.name}>"
    
    def get_etf_codes(self):
        """从allocation_config提取ETF代码列表"""
        if self.allocation_config:
            return list(self.allocation_config.keys())
        return []
