"""个股基本面与行业性价比模型 - 赢利-估值性价比研究（二期）"""

from sqlalchemy import Column, Integer, String, Date, Float, UniqueConstraint
from app.db.database import Base


class StockFundamental(Base):
    """池内个股每日基本面快照（估值 + 申万/证监会行业 + 最新季度增速冗余）"""
    __tablename__ = "stock_fundamental"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_stock_fund_code_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(12), nullable=False, index=True, comment="baostock代码: sh.600519")
    stock_name = Column(String(30), nullable=True, comment="股票名称")
    trade_date = Column(Date, nullable=False, index=True, comment="交易日期")
    industry = Column(String(30), nullable=True, comment="行业（证监会分类中文名）")
    close = Column(Float, nullable=True, comment="收盘价")
    pe_ttm = Column(Float, nullable=True, comment="市盈率TTM（亏损为空）")
    pb_mrq = Column(Float, nullable=True, comment="市净率MRQ")
    ni_yoy = Column(Float, nullable=True, comment="最新报告期净利润同比%")
    growth_stat_date = Column(Date, nullable=True, comment="增速对应的报告期")

    def __repr__(self):
        return f"<StockFundamental {self.stock_code} {self.trade_date} PE={self.pe_ttm}>"


class IndustryScore(Base):
    """行业盈利-估值性价比日度评分排名"""
    __tablename__ = "industry_score"
    __table_args__ = (
        UniqueConstraint("industry", "trade_date", name="uq_industry_score_ind_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False, index=True, comment="交易日期")
    industry = Column(String(30), nullable=False, comment="行业名")
    sample_count = Column(Integer, nullable=True, comment="有效样本股数")
    median_pe = Column(Float, nullable=True, comment="行业中位PE-TTM")
    median_growth = Column(Float, nullable=True, comment="行业中位净利润同比%")
    peg = Column(Float, nullable=True, comment="中位PE/中位增速")
    pe_percentile = Column(Float, nullable=True, comment="行业中位PE的历史分位(0-100)")
    trend_momentum = Column(Float, nullable=True, comment="匹配行业ETF的20日动量%")
    score = Column(Float, nullable=True, comment="性价比综合得分0-100")
    rank = Column(Integer, nullable=True, comment="得分排名")

    def __repr__(self):
        return f"<IndustryScore {self.trade_date} {self.industry} score={self.score}>"
