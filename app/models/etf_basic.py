from sqlalchemy import Column, String, VARCHAR, Date, DateTime, func
from datetime import datetime
from app.db.database import Base


class ETFBasic(Base):
    """ETF基础信息表"""
    
    __tablename__ = "etf_basic"
    
    etf_code = Column(VARCHAR(10), primary_key=True, comment="ETF代码")
    etf_name = Column(String(50), nullable=False, comment="ETF名称")
    issuer = Column(String(50), nullable=True, comment="发行机构")
    establish_date = Column(Date, nullable=True, comment="成立日期")
    update_time = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")
    
    def __repr__(self):
        return f"<ETFBasic(code={self.etf_code}, name={self.etf_name})>"
