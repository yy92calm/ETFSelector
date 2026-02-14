"""策略注册表：管理所有内置和AI生成的策略"""

from typing import Dict, Optional, Type
from app.strategies.base import BaseStrategy
from app.strategies.templates.ma_cross import MACrossStrategy
from app.strategies.templates.macd import MACDStrategy
from app.strategies.templates.rsi import RSIStrategy
from app.strategies.templates.momentum import MomentumStrategy
from app.strategies.templates.bollinger import BollingerStrategy

# 模板策略注册
TEMPLATE_REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "ma_cross": MACrossStrategy,
    "macd": MACDStrategy,
    "rsi": RSIStrategy,
    "momentum": MomentumStrategy,
    "bollinger": BollingerStrategy,
}


def get_template_strategy(template_name: str, params: Optional[dict] = None) -> BaseStrategy:
    """根据模板名创建策略实例"""
    cls = TEMPLATE_REGISTRY.get(template_name)
    if cls is None:
        raise ValueError(f"未知策略模板: {template_name}，可选: {list(TEMPLATE_REGISTRY.keys())}")
    return cls(params=params)


def list_templates() -> list:
    """列出所有可用模板"""
    result = []
    for name, cls in TEMPLATE_REGISTRY.items():
        instance = cls()
        result.append({
            "template_name": name,
            "description": instance.description,
            "default_params": instance.default_params,
        })
    return result
