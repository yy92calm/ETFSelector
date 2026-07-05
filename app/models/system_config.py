"""系统配置模型 - 运行时可修改的配置项（key-value）"""

from sqlalchemy import Column, String, Text, DateTime
from datetime import datetime
from app.db.database import Base


class SystemConfig(Base):
    """系统配置表 - 存储运行时可修改的配置（如 LLM 参数）"""
    __tablename__ = "system_config"

    key = Column(String(50), primary_key=True, comment="配置键名")
    value = Column(Text, nullable=True, comment="配置值")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<SystemConfig {self.key}>"
