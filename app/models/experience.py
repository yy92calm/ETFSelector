"""策略经验模型 - AI复盘生成的结构化经验"""

from sqlalchemy import Column, String, Integer, Float, Date, DateTime, ForeignKey, JSON, Boolean, Text
from datetime import datetime, timedelta
from app.db.database import Base


class Experience(Base):
    """策略经验表 - 存储AI总结的结构化经验"""
    __tablename__ = "experience"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey('strategy.id'), nullable=True, index=True, comment="关联策略ID，空表示系统级经验")
    
    # 经验分类
    experience_type = Column(String(20), nullable=False, comment="经验类型: success/failure/insight")
    scenario_tags = Column(JSON, nullable=False, comment="场景标签列表: ['高通胀', '政策收紧']")
    
    # 经验内容
    title = Column(String(100), nullable=False, comment="简明标题")
    description = Column(Text, nullable=False, comment="详细描述")
    market_condition = Column(JSON, nullable=True, comment="市场环境关键指标")
    action_taken = Column(JSON, nullable=True, comment="采取/应采取的行动")
    result = Column(String(20), nullable=False, comment="结果: positive/negative/neutral")
    key_insight = Column(String(200), nullable=True, comment="核心洞察（一句话）")
    
    # 有效性指标
    effectiveness_score = Column(Float, default=0.0, comment="效果评分: 0-10分")
    application_count = Column(Integer, default=0, comment="应用次数")
    success_count = Column(Integer, default=0, comment="成功应用次数")
    failure_count = Column(Integer, default=0, comment="失败应用次数")
    success_rate = Column(Float, nullable=True, comment="应用成功率")
    
    # 来源
    source_log_id = Column(Integer, nullable=True, comment="来源日志ID")
    source_type = Column(String(20), default="review", comment="来源类型: review/manual/imported")
    generated_date = Column(Date, nullable=False, comment="生成日期")
    
    # 生命周期
    is_validated = Column(Boolean, default=False, comment="是否已验证")
    is_active = Column(Boolean, default=True, comment="是否激活")
    expires_date = Column(Date, nullable=False, comment="过期日期")
    weight = Column(Float, default=1.0, comment="权重（衰减）")
    
    # 审核
    review_status = Column(String(20), default="pending", comment="审核状态: pending/approved/rejected")
    reviewed_by = Column(String(50), nullable=True, comment="审核人")
    reviewed_at = Column(DateTime, nullable=True, comment="审核时间")

    # 失败模式库字段（FSA式规避）
    failure_signature = Column(String(200), nullable=True, comment="失败模式签名，如 买入510300后5日亏损")
    occurrence_count = Column(Integer, default=1, comment="失败模式出现次数")
    last_triggered_date = Column(Date, nullable=True, comment="最近触发日期")

    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")
    
    def __repr__(self):
        return f"<Experience {self.id}: {self.experience_type} - {self.title[:30]}...>"
    
    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "strategy_id": self.strategy_id,
            "experience_type": self.experience_type,
            "scenario_tags": self.scenario_tags,
            "title": self.title,
            "description": self.description,
            "market_condition": self.market_condition,
            "action_taken": self.action_taken,
            "result": self.result,
            "key_insight": self.key_insight,
            "effectiveness_score": self.effectiveness_score,
            "application_count": self.application_count,
            "success_rate": self.success_rate,
            "is_validated": self.is_validated,
            "is_active": self.is_active,
            "weight": self.weight,
            "expires_date": self.expires_date.isoformat() if self.expires_date else None,
            "generated_date": self.generated_date.isoformat() if self.generated_date else None,
        }
    
    @staticmethod
    def get_default_expires_date():
        """获取默认过期日期（90天后）"""
        return datetime.now().date() + timedelta(days=90)


class ExperienceUsageRecord(Base):
    """经验应用记录"""
    __tablename__ = "experience_usage_record"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    experience_id = Column(Integer, ForeignKey('experience.id'), nullable=False, index=True)
    strategy_id = Column(Integer, ForeignKey('strategy.id'), nullable=False, index=True)
    
    # 应用场景
    usage_date = Column(Date, nullable=False, index=True, comment="应用日期")
    market_condition = Column(JSON, nullable=True, comment="当时市场环境")
    decision_made = Column(JSON, nullable=True, comment="做出的决策")
    
    # 应用结果
    result = Column(String(20), nullable=True, comment="结果: positive/negative/neutral")
    return_pct = Column(Float, nullable=True, comment="收益率%")
    outcome_detail = Column(Text, nullable=True, comment="结果详情")
    
    # 验证
    is_validated = Column(Boolean, default=False, comment="是否已验证")
    validated_at = Column(DateTime, nullable=True, comment="验证时间")
    
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    
    def __repr__(self):
        return f"<ExperienceUsageRecord {self.experience_id} on {self.usage_date}: {self.result}>"