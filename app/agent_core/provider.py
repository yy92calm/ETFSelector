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


def stream_completion(client, model: str, messages: list, tools=None,
                      temperature: float = 0.3, max_tokens: int = 2000):
    """流式调用LLM，逐chunk产出。

    参照 deepseek-harness StreamChunk 协议（types.ts:291-303），
    简化为4种事件类型：

    - thinking_delta:  思考增量（对应harness reasoning-delta）
    - text_delta:      正文增量（对应harness text-delta）
    - tool_calls:      完整工具调用列表（harness tool-call-delta累积后的结果）
    - done:            流结束（对应harness finish）

    Qwen的<think>标签通过状态机解析（harness用DeepSeek的reasoning_content原生字段）。
    tool_calls延迟到流结束统一发出（参照harness translate.ts延迟关闭模式）。
    """
    response = client.chat.completions.create(
        model=model, messages=messages, tools=tools,
        temperature=temperature, max_tokens=max_tokens,
        stream=True,
    )
    in_thinking = False
    tool_calls_buf = {}

    try:
        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if not delta:
                continue

            content = getattr(delta, "content", None) or ""
            if content:
                # Qwen <think>标签解析（状态机，处理跨chunk拆分）
                if "<think>" in content:
                    in_thinking = True
                    tag_stripped = content.replace("<think>", "", 1)
                    if tag_stripped:
                        yield {"type": "thinking_delta", "content": tag_stripped}
                elif in_thinking and "</think>" in content:
                    before, after = content.split("</think>", 1)
                    if before:
                        yield {"type": "thinking_delta", "content": before}
                    in_thinking = False
                    yield {"type": "thinking_done", "content": ""}
                    if after:
                        yield {"type": "text_delta", "content": after}
                elif in_thinking:
                    yield {"type": "thinking_delta", "content": content}
                else:
                    yield {"type": "text_delta", "content": content}

            # tool_calls增量累积（参照harness assembler.ts index稀疏数组模式）
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_buf:
                        tool_calls_buf[idx] = {
                            "id": "", "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_calls_buf[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_buf[idx]["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_buf[idx]["function"]["arguments"] += tc.function.arguments

            if chunk.choices[0].finish_reason:
                break
    except Exception as e:
        logger.error(f"LLM流式调用异常: {e}")
        yield {"type": "error", "error": str(e)}
        return

    # 延迟发送tool_calls和finish（参照harness translate.ts延迟关闭模式）
    if tool_calls_buf:
        yield {"type": "tool_calls", "tool_calls": list(tool_calls_buf.values())}
    yield {"type": "done"}
