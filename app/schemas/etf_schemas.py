"""
ETF相关的Pydantic schema
用于API请求/响应数据验证
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date, datetime


class ETFBasicSchema(BaseModel):
    """ETF基础信息schema"""
    etf_code: str = Field(..., description="ETF代码")
    etf_name: str = Field(..., description="ETF名称")
    issuer: Optional[str] = Field(None, description="发行机构")
    establish_date: Optional[date] = Field(None, description="成立日期")
    
    class Config:
        from_attributes = True


class ETFQuotationSchema(BaseModel):
    """ETF行情数据schema"""
    id: Optional[int] = Field(None, description="主键ID")
    etf_code: str = Field(..., description="ETF代码")
    trade_date: date = Field(..., description="交易日期")
    open_price: float = Field(..., description="开盘价")
    close_price: float = Field(..., description="收盘价")
    high_price: float = Field(..., description="最高价")
    low_price: float = Field(..., description="最低价")
    volume: int = Field(..., description="成交量")
    amount: float = Field(..., description="成交额")
    change_rate: float = Field(..., description="涨跌幅")
    
    class Config:
        from_attributes = True


class ETFDetailSchema(BaseModel):
    """ETF详细信息schema（包含基础信息和最新行情）"""
    etf_code: str = Field(..., description="ETF代码")
    etf_name: str = Field(..., description="ETF名称")
    last_price: float = Field(..., description="最新价格")
    change_rate: float = Field(..., description="涨跌幅")
    volume: int = Field(..., description="成交量")
    amount: float = Field(..., description="成交额")


class FetchQuoteRequest(BaseModel):
    """获取行情请求schema"""
    etf_codes: List[str] = Field(..., min_items=1, max_items=100, description="ETF代码列表")


class APIResponse(BaseModel):
    """统一API响应schema"""
    code: int = Field(..., description="状态码")
    message: str = Field(..., description="消息")
    data: Optional[dict] = Field(None, description="数据")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="时间戳")
