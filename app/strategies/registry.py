"""策略注册表：管理配置组合模板"""

from typing import Dict, Optional, Type
from app.strategies.templates.conservative import ConservativeAllocation
from app.strategies.templates.balanced import BalancedAllocation
from app.strategies.templates.aggressive import AggressiveAllocation

# 配置模板注册
TEMPLATE_REGISTRY: Dict[str, Type] = {
    "conservative": ConservativeAllocation,
    "balanced": BalancedAllocation,
    "aggressive": AggressiveAllocation,
}


def get_template_config(template_name: str) -> dict:
    """根据模板名获取配置信息"""
    cls = TEMPLATE_REGISTRY.get(template_name)
    if cls is None:
        raise ValueError(f"未知配置模板: {template_name}，可选: {list(TEMPLATE_REGISTRY.keys())}")
    
    instance = cls()
    return instance.get_info()


def list_templates() -> list:
    """列出所有可用配置模板"""
    result = []
    for name, cls in TEMPLATE_REGISTRY.items():
        instance = cls()
        result.append(instance.get_info())
    return result
