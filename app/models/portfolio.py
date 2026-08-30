"""组合 / 持仓 / 交易记录模型"""

from sqlalchemy import Column, String, Integer, Float, Date, DateTime, ForeignKey
from datetime import datetime
from app.db.database import Base


class PortfolioSnapshot(Base):
    """每日组合快照（每日资产记录）"""
    __tablename__ = "portfolio_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategy.id"), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    total_asset = Column(Float, nullable=False, default=0, comment="总资产")
    cash = Column(Float, nullable=False, default=0, comment="可用现金")
    market_value = Column(Float, nullable=False, default=0, comment="持仓市值")
    profit = Column(Float, nullable=False, default=0, comment="当日盈亏")
    profit_pct = Column(Float, nullable=False, default=0, comment="累计收益率%")

    def __repr__(self):
        return f"<Snapshot strategy={self.strategy_id} {self.trade_date} asset={self.total_asset}>"


class TradeRecord(Base):
    """交易记录"""
    __tablename__ = "trade_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategy.id"), nullable=False, index=True)
    trade_date = Column(Date, nullable=False)
    etf_code = Column(String(10), nullable=False)
    direction = Column(String(4), nullable=False, comment="buy / sell")
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False, comment="交易数量(股)")
    amount = Column(Float, nullable=False, comment="交易金额")
    fee = Column(Float, default=0.0, comment="单笔手续费")
    reason = Column(String(200), nullable=True, comment="交易原因")
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Trade {self.direction} {self.etf_code} qty={self.quantity}>"


class HoldingSnapshot(Base):
    """每日持仓快照（按实际日期保留的历史持仓记录）"""
    __tablename__ = "holding_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategy.id"), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    etf_code = Column(String(10), nullable=False)
    quantity = Column(Integer, nullable=False, default=0, comment="持仓数量")
    price = Column(Float, nullable=False, default=0, comment="当日估值价格")
    market_value = Column(Float, nullable=False, default=0, comment="市值")

    def __repr__(self):
        return f"<HoldingSnapshot strategy={self.strategy_id} {self.trade_date} {self.etf_code} qty={self.quantity}>"


class Holding(Base):
    """当前持仓"""
    __tablename__ = "holding"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey("strategy.id"), nullable=False, index=True)
    etf_code = Column(String(10), nullable=False)
    quantity = Column(Integer, nullable=False, default=0, comment="持仓数量")
    avg_cost = Column(Float, nullable=False, default=0, comment="平均成本")
    current_price = Column(Float, nullable=False, default=0, comment="当前价格")
    market_value = Column(Float, nullable=False, default=0, comment="市值")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
