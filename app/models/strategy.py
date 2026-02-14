"""策略模型"""

from sqlalchemy import Column, String, Integer, Text, DateTime, JSON
from datetime import datetime
from app.db.database import Base


class Strategy(Base):
    """交易策略"""
    __tablename__ = "strategy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="策略名称")
    description = Column(Text, nullable=True, comment="策略描述（自然语言）")
    strategy_type = Column(String(30), nullable=False, default="template", comment="template / ai_generated")
    template_name = Column(String(50), nullable=True, comment="模板名称（模板策略时使用）")
    params = Column(JSON, nullable=True, comment="策略参数 JSON")
    code = Column(Text, nullable=True, comment="策略代码（AI生成时使用）")
    etf_codes = Column(JSON, nullable=True, comment="关联ETF代码列表")
    initial_capital = Column(Integer, nullable=False, default=100000, comment="初始资金")
    status = Column(String(20), nullable=False, default="active", comment="active / paused / archived")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Strategy {self.id}: {self.name}>"
