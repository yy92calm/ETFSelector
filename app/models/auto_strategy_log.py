"""自动策略日志模型"""

from sqlalchemy import Column, String, Integer, Float, Date, DateTime, ForeignKey, JSON, Boolean
from datetime import datetime
from app.db.database import Base


class AutoStrategyLog(Base):
    """自动策略执行日志"""
    __tablename__ = "auto_strategy_log"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey('strategy.id'), nullable=False, index=True)
    log_date = Column(Date, nullable=False, index=True, comment="执行日期")
    
    # 执行状态
    status = Column(String(20), nullable=False, comment="执行状态: success/failed/skipped")
    action_type = Column(String(20), nullable=False, comment="动作类型: analyze/adjust/hold")
    
    # 执行详情
    sentiment_summary = Column(JSON, nullable=True, comment="当日舆情汇总")
    analysis_result = Column(JSON, nullable=True, comment="AI分析结果")
    adjustment_decision = Column(JSON, nullable=True, comment="调整决策")
    
    # 安全检查
    safety_check_passed = Column(Boolean, default=True, comment="安全检查是否通过")
    safety_reason = Column(String(200), nullable=True, comment="未通过原因")
    
    # 执行结果
    old_allocation = Column(JSON, nullable=True, comment="调整前配置")
    new_allocation = Column(JSON, nullable=True, comment="调整后配置")
    allocation_change = Column(Float, nullable=True, comment="配置变化幅度")
    
    # 持仓变化
    old_holdings = Column(JSON, nullable=True, comment="调整前持仓")
    new_holdings = Column(JSON, nullable=True, comment="调整后持仓")
    
    # 错误信息
    error_message = Column(String(500), nullable=True, comment="错误信息")
    
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    
    def __repr__(self):
        return f"<AutoStrategyLog {self.strategy_id} {self.log_date}: {self.status}>"
    
    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "strategy_id": self.strategy_id,
            "log_date": self.log_date.isoformat() if self.log_date else None,
            "status": self.status,
            "action_type": self.action_type,
            "sentiment_summary": self.sentiment_summary,
            "analysis_result": self.analysis_result,
            "adjustment_decision": self.adjustment_decision,
            "safety_check_passed": self.safety_check_passed,
            "safety_reason": self.safety_reason,
            "old_allocation": self.old_allocation,
            "new_allocation": self.new_allocation,
            "allocation_change": self.allocation_change,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }