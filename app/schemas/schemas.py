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
    """创建配置组合策略"""
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    strategy_type: str = Field(default="template", pattern="^(template|ai_generated|custom)$")
    
    # 模板创建时可选（从模板获取）
    template_name: Optional[str] = Field(None, description="模板名称：conservative/balanced/aggressive")
    
    # 自定义创建时必填
    allocation_config: Optional[dict] = Field(None, description="ETF配置比例，如 {'510300': 0.5, '511010': 0.4}")
    rebalance_freq: str = Field(default="quarterly", description="再平衡频率：monthly/quarterly/yearly")
    rebalance_threshold: float = Field(default=0.05, ge=0.01, le=0.2, description="偏离阈值")
    
    initial_capital: int = Field(default=100000, ge=10000)


class StrategyOut(BaseModel):
    """策略输出"""
    id: int
    name: str
    description: Optional[str]
    strategy_type: str
    
    # 配置组合字段
    allocation_config: Optional[dict]
    rebalance_freq: Optional[str]
    rebalance_threshold: Optional[float]
    
    # 其他字段
    code: Optional[str]
    initial_capital: int
    status: str
    created_at: datetime
    
    # 旧字段兼容
    template_name: Optional[str] = None
    params: Optional[dict] = None
    etf_codes: Optional[List[str]] = None
    
    model_config = {"from_attributes": True}


class AIChatRequest(BaseModel):
    """AI对话式生成策略请求"""
    message: str = Field(..., min_length=5, max_length=500, description="用户消息")
    chat_history: Optional[str] = Field(None, description="对话历史")
    current_allocation: Optional[dict] = Field(None, description="当前配置方案")
    model: str = Field(default="qwen3.6-plus", description="使用的AI模型")


class AIStrategyRequest(BaseModel):
    """AI策略生成请求"""
    description: str = Field(
        ..., 
        min_length=5, 
        max_length=1000, 
        description="自然语言描述配置偏好，如：我要一个保守组合，债券为主，少量股票和黄金"
    )
    initial_capital: int = Field(default=100000, ge=10000)
    model: str = Field(default="qwen3.6-plus", description="使用的AI模型（阿里云DashScope）")
    rebalance_freq: str = Field(default="quarterly", description="再平衡频率")
    rebalance_threshold: float = Field(default=0.05, ge=0.01, le=0.2, description="偏离阈值")


class StrategyUpdate(BaseModel):
    """策略更新请求"""
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    
    # 配置组合字段
    allocation_config: Optional[dict] = None
    rebalance_freq: Optional[str] = None
    rebalance_threshold: Optional[float] = Field(None, ge=0.01, le=0.2)
    
    initial_capital: Optional[int] = Field(None, ge=10000)
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
