"""ETF基础信息 & 行情数据 & 量化指标模型"""

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


class ETFDailyIndicator(Base):
    """ETF每日量化指标（全市场全量计算存库）"""
    __tablename__ = "etf_daily_indicator"
    __table_args__ = (
        Index("ix_indicator_code_date", "etf_code", "trade_date", unique=True),
        Index("ix_indicator_date_score", "trade_date", "composite_score"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    etf_code = Column(String(10), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)

    momentum_5d = Column(Float, default=0, comment="5日收益率%")
    momentum_20d = Column(Float, default=0, comment="20日收益率%")
    momentum_score = Column(Float, default=0, comment="动量综合得分")

    trend_strength = Column(Integer, default=0, comment="趋势强度0-3（价格位于MA5/10/20之上数量）")
    ma5 = Column(Float, default=0)
    ma10 = Column(Float, default=0)
    ma20 = Column(Float, default=0)

    vol_ratio = Column(Float, default=0, comment="5日均量/20日均量")
    volatility_20d = Column(Float, default=0, comment="20日年化波动率%")

    obv_slope = Column(Float, default=0, comment="OBV 5日斜率方向")
    amount_avg_5d = Column(Float, default=0, comment="5日均成交额")

    composite_score = Column(Float, default=0, comment="综合得分（加权）")
    rank_in_market = Column(Integer, default=0, comment="全市场排名")

    def __repr__(self):
        return f"<Indicator {self.etf_code} {self.trade_date} score={self.composite_score}>"
