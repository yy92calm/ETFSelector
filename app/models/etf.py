"""ETF基础信息 & 行情数据模型"""

from sqlalchemy import Column, String, Float, Integer, Date, DateTime, Index
from datetime import datetime
from app.db.database import Base


class ETFBasic(Base):
    """ETF基础信息"""
    __tablename__ = "etf_basic"

    etf_code = Column(String(10), primary_key=True, comment="ETF代码，如 sh510050")
    etf_name = Column(String(80), nullable=False, default="", comment="ETF名称")
    fund_type = Column(String(30), nullable=True, comment="基金类型")
    update_time = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ETF {self.etf_code} {self.etf_name}>"


class ETFQuotation(Base):
    """ETF日行情"""
    __tablename__ = "etf_quotation"
    __table_args__ = (
        Index("ix_quotation_code_date", "etf_code", "trade_date", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    etf_code = Column(String(10), nullable=False, index=True, comment="ETF代码")
    trade_date = Column(Date, nullable=False, index=True, comment="交易日期")
    open_price = Column(Float, nullable=False, default=0)
    close_price = Column(Float, nullable=False, default=0)
    high_price = Column(Float, nullable=False, default=0)
    low_price = Column(Float, nullable=False, default=0)
    volume = Column(Float, nullable=False, default=0, comment="成交量")
    amount = Column(Float, nullable=False, default=0, comment="成交额")
    change_pct = Column(Float, nullable=False, default=0, comment="涨跌幅%")

    def __repr__(self):
        return f"<Quote {self.etf_code} {self.trade_date} close={self.close_price}>"
