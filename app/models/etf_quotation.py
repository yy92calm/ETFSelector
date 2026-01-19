from sqlalchemy import Column, Integer, String, Float, Date, DateTime, ForeignKey
from datetime import datetime
from app.db.database import Base


class ETFQuotation(Base):
    """ETF行情数据表"""
    
    __tablename__ = "etf_quotation"
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键ID")
    etf_code = Column(String(10), ForeignKey("etf_basic.etf_code"), nullable=False, comment="ETF代码")
    trade_date = Column(Date, nullable=False, comment="交易日期")
    open_price = Column(Float, nullable=False, comment="开盘价")
    close_price = Column(Float, nullable=False, comment="收盘价")
    high_price = Column(Float, nullable=False, comment="最高价")
    low_price = Column(Float, nullable=False, comment="最低价")
    volume = Column(Integer, nullable=False, default=0, comment="成交量")
    amount = Column(Float, nullable=False, default=0.0, comment="成交额")
    change_rate = Column(Float, nullable=False, default=0.0, comment="涨跌幅")
    update_time = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")
    
    def __repr__(self):
        return f"<ETFQuotation(code={self.etf_code}, date={self.trade_date}, close={self.close_price})>"
