"""调仓建议模型 - LLM 只出建议，实际换仓统一走轮动通道（rotation_service）

一轮建议的生命周期：
  pending（LLM/人工提交） → adopted（轮动通道采纳并落地待生效配置）
                          → rejected（轮动裁决驳回，附理由）
                          → 过期不处理则视为 rejected（由轮动通道在消费时结算）
"""

from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, Date, ForeignKey, Integer, JSON, String, Text

from app.db.database import Base


class AllocationSuggestion(Base):
    """组合调仓建议（不改配置，仅供轮动通道裁决）"""

    __tablename__ = "allocation_suggestion"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategy.id"), nullable=False, index=True)

    source = Column(String(20), nullable=False, default="agentloop",
                    comment="来源: agentloop（自主决策）/ fallback（降级管道·未使用LLM）/ chat（对话）/ manual（人工）")
    suggested_allocation = Column(JSON, nullable=False, comment="建议配置 {etf_code: weight}（总和1.0）")
    reason = Column(Text, nullable=True, comment="建议理由")

    status = Column(String(12), nullable=False, default="pending", index=True,
                    comment="pending / adopted / rejected")
    decided_at = Column(DateTime, nullable=True, comment="轮动通道裁决时间")
    decided_note = Column(Text, nullable=True, comment="裁决说明（采纳/驳回理由）")

    created_at = Column(DateTime, default=datetime.utcnow)

    @staticmethod
    def default_expires_days() -> int:
        # 有效期（自然日）：需 > rotation_service.MIN_HOLD_DAYS(5)，否则建议会在持仓可换出前过期
        return 7

    def __repr__(self):
        return f"<AllocationSuggestion {self.id} strategy={self.strategy_id} {self.status}>"