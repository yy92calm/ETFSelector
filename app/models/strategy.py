"""策略模型 - ETF配置组合"""

from sqlalchemy import Column, String, Integer, Text, DateTime, JSON, Float, Boolean, Date
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
    
    # ===== AI全自动策略新增字段 =====
    strategy_source = Column(String(20), default='manual', comment="策略来源: manual/auto_generated")
    auto_strategy_status = Column(String(20), nullable=True, comment="自动策略状态: running/paused/stopped")
    last_auto_analysis_date = Column(Date, nullable=True, comment="最近自动分析日期")
    auto_adjustment_count = Column(Integer, default=0, comment="自动调整累计次数")
    max_daily_adjustments = Column(Integer, default=1, comment="每日最大调整次数")
    
    # AI分析结果缓存
    last_analysis_result = Column(JSON, nullable=True, comment="最近AI分析结果")
    
    # 记忆机制配置
    enable_memory = Column(Boolean, default=True, comment="是否启用记忆机制")
    experience_limit = Column(Integer, default=50, comment="最大经验条数")

    # 持仓起始日期（首次建仓日，用于跟踪实际收益）
    holding_start_date = Column(Date, nullable=True, comment="持仓起始日期（首次建仓日）")

    # 风控暂停记录
    paused_reason = Column(String(200), nullable=True, comment="策略暂停原因")
    paused_date = Column(Date, nullable=True, comment="策略暂停日期")

    def __repr__(self):
        return f"<Strategy {self.id}: {self.name}>"
    
    def get_etf_codes(self):
        """从allocation_config提取ETF代码列表"""
        if self.allocation_config:
            return list(self.allocation_config.keys())
        return []
    
    def is_auto_strategy(self):
        """判断是否为自动策略"""
        return self.strategy_source == 'auto_generated'
    
    def can_adjust_today(self):
        """判断今日是否还能调整"""
        return self.auto_adjustment_count < self.max_daily_adjustments
