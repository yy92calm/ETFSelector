"""因子表现记录模型 - 跟踪各因子值与未来收益的相关性（IC）"""

from sqlalchemy import Column, Integer, String, Date, Float, DateTime
from datetime import datetime
from app.db.database import Base


class FactorPerformance(Base):
    """因子表现记录 - 用于计算IC和动态权重"""
    __tablename__ = "factor_performance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    etf_code = Column(String(20), nullable=False, index=True, comment="ETF代码")
    trade_date = Column(Date, nullable=False, index=True, comment="指标日期")
    factor_name = Column(String(30), nullable=False, comment="因子名: momentum/trend/volume/volatility/capital_flow")
    factor_value = Column(Float, nullable=True, comment="因子得分 0-100")
    forward_return_5d = Column(Float, nullable=True, comment="未来5日收益率%")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")

    def __repr__(self):
        return f"<FactorPerformance {self.etf_code} {self.trade_date} {self.factor_name}: {self.factor_value}>"
