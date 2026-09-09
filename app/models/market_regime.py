"""市场状态刻画快照 - 风险偏好/风格轮动/基金收益率分化度的中期刻画与仓位信号"""

from sqlalchemy import Column, Integer, String, Date, Float, JSON, DateTime, Text
from datetime import datetime
from app.db.database import Base


class MarketRegimeSnapshot(Base):
    """每日市场状态快照（由每日管道 market_regime 阶段生成）"""
    __tablename__ = "market_regime_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, unique=True, index=True, comment="交易日期")
    risk_appetite = Column(Float, nullable=True, comment="风险偏好指数 0-100")
    risk_label = Column(String(10), nullable=True, comment="风险偏好标签: 保守/中性/进取")
    style_rotation = Column(JSON, nullable=True, comment="风格轮动: {size:{leading,spread}, growth_value:{leading,spread}}")
    fund_dispersion = Column(Float, nullable=True, comment="偏股基金60日收益截面标准差%")
    dispersion_label = Column(String(10), nullable=True, comment="分化度标签: 一致/适度/分化")
    market_state = Column(String(20), nullable=True, comment="市场状态: opportunity/risk/neutral")
    state_label = Column(String(20), nullable=True, comment="市场状态标签: 机会/风险/中性")
    state_note = Column(Text, nullable=True, comment="状态说明（各维度组合逻辑）")
    suggested_equity_range = Column(JSON, nullable=True, comment="建议权益仓位区间 [min, max]")
    details = Column(JSON, nullable=True, comment="各分量明细（代理对动量/热度分位/样本量等）")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")

    def __repr__(self):
        return f"<MarketRegimeSnapshot {self.trade_date} 风险偏好{self.risk_appetite} {self.state_label}>"
