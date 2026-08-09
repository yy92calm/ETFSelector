"""Provider 抽象 - 模型前缀 → base_url/API Key 路由

精简移植 OpenWorker provider.py：
- 通过 llm_model_aliases 配置（JSON）按模型名前缀解析 provider（最长前缀优先）
- 支持会话级模型切换（POST /api/chat/model）
- 未命中别名时回退到 settings 默认 LLM 配置
"""

import json
import logging
from typing import Dict, Optional, Tuple

from app.config import get_settings

logger = logging.getLogger(__name__)


def parse_aliases() -> Dict[str, dict]:
    """解析 llm_model_aliases JSON 为 {前缀: {"base_url": str, "api_key": str|None}}

    支持两种 value 形态：
    - 字符串：仅 base_url，api_key 用 settings 默认
    - 对象：{"base_url": "...", "api_key": "..."}
    """
    raw = get_settings().llm_model_aliases or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"llm_model_aliases JSON 解析失败: {e}")
        return {}
    if not isinstance(data, dict):
        logger.error(f"llm_model_aliases 应为 JSON 对象，收到: {type(data).__name__}")
        return {}

    result: Dict[str, dict] = {}
    for prefix, value in data.items():
        if isinstance(value, str):
            result[prefix] = {"base_url": value, "api_key": None}
        elif isinstance(value, dict):
            result[prefix] = {
                "base_url": value.get("base_url", ""),
                "api_key": value.get("api_key"),
            }
    return result


def resolve_llm(model: str) -> Tuple[str, Optional[str], str]:
    """按模型名前缀解析 (base_url, api_key, 实际模型名)

    最长前缀优先；未命中任何别名时回退到 settings 默认配置。
    """
    settings = get_settings()
    aliases = parse_aliases()
    matched = None
    for prefix in aliases:
        if model.startswith(prefix) and (matched is None or len(prefix) > len(matched)):
            matched = prefix
    if matched:
        info = aliases[matched]
        return (
            info["base_url"] or settings.llm_api_base_url,
            info.get("api_key") or settings.llm_api_key,
            model,
        )
    return settings.llm_api_base_url, settings.llm_api_key, model


def build_openai_client(base_url: str, api_key: Optional[str]):
    """按解析结果构建 OpenAI 客户端；无 API Key 时返回 None"""
    if not api_key or not api_key.strip():
        return None
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url)
