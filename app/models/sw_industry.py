"""申万一级行业每日快照（板块轮动模型，口径借鉴 open-ai-workbench 宏观洞察）"""

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Float, Integer, String, UniqueConstraint

from app.db.database import Base


class SwIndustryDaily(Base):
    """申万一级行业（31 个）逐日行情/估值/换手/成交占比 + 板块轮动评分"""

    __tablename__ = "sw_industry_daily"
    __table_args__ = (
        UniqueConstraint("trade_date", "sw_code", name="uq_sw_ind_date_code"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, index=True, comment="数据截止日（分析日报口径）")
    sw_code = Column(String(8), nullable=False, index=True, comment="申万指数代码，如 801080")
    name = Column(String(20), nullable=False, comment="行业名称")

    # 行情与估值（来自逐日分析日报）
    close = Column(Float, nullable=True, comment="收盘指数")
    markup = Column(Float, nullable=True, comment="涨跌幅%")
    turnover_rate = Column(Float, nullable=True, comment="换手率%")
    pe = Column(Float, nullable=True, comment="市盈率")
    pb = Column(Float, nullable=True, comment="市净率")
    dividend_yield = Column(Float, nullable=True, comment="股息率%")
    amount_share = Column(Float, nullable=True, comment="成交额占比%（31行业合计≈100）")
    float_mcap = Column(Float, nullable=True, comment="流通市值（亿元）")

    # 板块轮动评分（0.45×相对强度分位 + 0.35×动量分位 + 0.20×趋势）
    ret60 = Column(Float, nullable=True, comment="60交易日收益（小数）")
    rs60 = Column(Float, nullable=True, comment="相对沪深300超额收益（小数）")
    trend = Column(Float, nullable=True, comment="收盘是否在20日均线上（1/0，不足20根为空）")
    vol60 = Column(Float, nullable=True, comment="60日年化波动率（小数）")
    score = Column(Integer, nullable=True, comment="板块评分0-100（≥67超配 / ≤33低配）")
    signal = Column(String(12), nullable=True, comment="overweight/neutral/underweight")
    score_delta = Column(Integer, nullable=True, comment="较上一评分日的变化")

    # 实时行情（index_publish/current，展示用，口径与日报截止日可能不同）
    live_change_pct = Column(Float, nullable=True, comment="实时涨跌幅%")

    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<SwIndustryDaily {self.trade_date} {self.name} score={self.score} {self.signal}>"