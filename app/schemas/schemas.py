"""Pydantic schemas for API validation"""

from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import date, datetime


# ---- 通用 ----
class APIResponse(BaseModel):
    code: int = 200
    message: str = "ok"
    data: Any = None


# ---- ETF ----
class ETFBasicOut(BaseModel):
    etf_code: str
    etf_name: str
    fund_type: Optional[str] = None
    model_config = {"from_attributes": True}


class ETFQuotationOut(BaseModel):
    etf_code: str
    trade_date: date
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float
    amount: float
    change_pct: float
    model_config = {"from_attributes": True}


# ---- 策略 ----
class StrategyCreate(BaseModel):
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    strategy_type: str = Field(default="template", pattern="^(template|ai_generated)$")
    template_name: Optional[str] = None
    params: Optional[dict] = None
    etf_codes: List[str] = Field(default_factory=list)
    initial_capital: int = Field(default=100000, ge=10000)


class StrategyOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    strategy_type: str
    template_name: Optional[str]
    params: Optional[dict]
    code: Optional[str]
    etf_codes: Optional[List[str]]
    initial_capital: int
    status: str
    created_at: datetime
    model_config = {"from_attributes": True}


class AIStrategyRequest(BaseModel):
    """AI策略生成请求"""
    description: str = Field(..., min_length=5, max_length=1000, description="自然语言描述策略逻辑")
    etf_codes: List[str] = Field(default_factory=list)
    initial_capital: int = Field(default=100000, ge=10000)
    model: str = Field(default="gpt-4o", description="使用的AI模型")


class StrategyUpdate(BaseModel):
    """策略更新请求"""
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    etf_codes: Optional[List[str]] = None
    initial_capital: Optional[int] = Field(None, ge=10000)
    params: Optional[dict] = None
    code: Optional[str] = None


# ---- 回测 ----
class BacktestRequest(BaseModel):
    strategy_id: int
    start_date: date
    end_date: date
    initial_capital: Optional[int] = None


class BacktestResult(BaseModel):
    strategy_id: int
    start_date: date
    end_date: date
    initial_capital: float
    final_asset: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: Optional[float] = None
    trade_count: int
    win_rate: Optional[float] = None
    daily_data: List[dict] = Field(default_factory=list, description="每日净值序列")
    trades: List[dict] = Field(default_factory=list)


# ---- 组合 ----
class PortfolioSnapshotOut(BaseModel):
    trade_date: date
    total_asset: float
    cash: float
    market_value: float
    profit: float
    profit_pct: float
    model_config = {"from_attributes": True}


class HoldingOut(BaseModel):
    etf_code: str
    quantity: int
    avg_cost: float
    current_price: float
    market_value: float
    model_config = {"from_attributes": True}
