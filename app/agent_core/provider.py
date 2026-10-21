"""Provider 抽象 - 模型前缀 → base_url/API Key 路由

精简移植 OpenWorker provider.py：
- 通过 llm_model_aliases 配置（JSON）按模型名前缀解析 provider（最长前缀优先）
- 支持会话级模型切换（POST /api/chat/model）
- 未命中别名时回退到 settings 默认 LLM 配置
"""

import json
import logging
import uuid
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
    import httpx

    return OpenAI(
        api_key=api_key, base_url=base_url,
        timeout=httpx.Timeout(connect=30, read=900, write=30, pool=30),
    )


def _partial_tag_suffix(text: str, tag: str) -> str:
    """返回 text 末尾可能是 tag 前缀的最长片段（跨 chunk 标签暂存用）"""
    for size in range(min(len(text), len(tag) - 1), 0, -1):
        if text.endswith(tag[:size]):
            return text[-size:]
    return ""


def _usage_to_dict(usage) -> dict:
    """把 OpenAI usage 对象转成普通 dict（缺字段填 None，便于落库）"""
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def stream_completion(client, model: str, messages: list, tools=None,
                      temperature: float = 0.3, max_tokens: int = 2000):
    """流式调用LLM，逐chunk产出。

    参照 deepseek-harness StreamChunk 协议（types.ts:291-303），
    简化为6种事件类型：

    - thinking_delta:  思考增量（对应harness reasoning-delta）
    - thinking_done:   思考结束（流未输出</think>闭合时在收尾兜底发出）
    - text_delta:      正文增量（对应harness text-delta）
    - tool_calls:      完整工具调用列表（harness tool-call-delta累积后的结果）
    - usage:           本轮token用量与finish_reason（对应harness usage块，done前必达区）
    - done:            流结束（对应harness finish）

    <think>标签用带缓冲的状态机解析：跨 chunk 的标签前缀先暂存，流结束再 flush；
    同时支持 DeepSeek 风格的 delta.reasoning_content 原生思考字段。
    tool_calls延迟到流结束统一发出（参照harness translate.ts延迟关闭模式），
    缺失 id 时补 call_{index}_{uuid8}，保证 tool 消息能配对。
    finish_reason后不立即break：usage通常在尾部的空choices块中，消费到流自然结束。
    """
    tool_calls_buf = {}
    finish_reason = None
    usage = None
    content_pending = ""
    in_think = False
    thinking_open = False

    def _feed_content(content: str):
        """解析正文中的 <think> 标签，返回事件列表；未决前缀留在 content_pending"""
        nonlocal content_pending, in_think, thinking_open
        content_pending += content
        events = []
        while content_pending:
            if in_think:
                idx = content_pending.find("</think>")
                if idx < 0:
                    held = _partial_tag_suffix(content_pending, "</think>")
                    emit = content_pending[:len(content_pending) - len(held)]
                    content_pending = held
                    if emit:
                        events.append({"type": "thinking_delta", "content": emit})
                    break
                before = content_pending[:idx]
                content_pending = content_pending[idx + len("</think>"):]
                if before:
                    events.append({"type": "thinking_delta", "content": before})
                in_think = False
                thinking_open = False
                events.append({"type": "thinking_done", "content": ""})
            else:
                idx = content_pending.find("<think>")
                if idx < 0:
                    held = _partial_tag_suffix(content_pending, "<think>")
                    emit = content_pending[:len(content_pending) - len(held)]
                    content_pending = held
                    if emit:
                        if thinking_open:
                            events.append({"type": "thinking_done", "content": ""})
                            thinking_open = False
                        events.append({"type": "text_delta", "content": emit})
                    break
                before = content_pending[:idx]
                content_pending = content_pending[idx + len("<think>"):]
                if before:
                    if thinking_open:
                        events.append({"type": "thinking_done", "content": ""})
                        thinking_open = False
                    events.append({"type": "text_delta", "content": before})
                in_think = True
                thinking_open = True
        return events

    def _create(extra_kwargs: dict):
        return client.chat.completions.create(
            model=model, messages=messages, tools=tools,
            temperature=temperature, max_tokens=max_tokens,
            stream=True,
            **extra_kwargs,
        )

    try:
        try:
            # include_usage：OpenAI/DeepSeek/DashScope 兼容端支持，尾块返回用量
            response = _create({"stream_options": {"include_usage": True}})
        except Exception as e:
            # 部分兼容端不认识 stream_options（400）：去掉后重试一次
            if getattr(e, "status_code", None) == 400:
                logger.warning("stream_options 不被支持（400），降级为不带 usage 请求")
                response = _create({})
            else:
                raise
        for chunk in response:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = _usage_to_dict(chunk_usage)
            if not chunk.choices:
                continue
            if finish_reason:
                # 已结束：不再处理增量，只等尾部 usage 块
                continue
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason
                continue
            delta = chunk.choices[0].delta
            if not delta:
                continue

            # DeepSeek 风格原生思考字段：直接作为 thinking_delta
            reasoning = getattr(delta, "reasoning_content", None) or ""
            if reasoning:
                thinking_open = True
                yield {"type": "thinking_delta", "content": reasoning}

            content = getattr(delta, "content", None) or ""
            if content:
                for event in _feed_content(content):
                    yield event

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
    except Exception as e:
        logger.error(f"LLM流式调用异常: {e}")
        yield {"type": "error", "error": str(e)}
        return

    # flush 暂存的标签前缀；仍处于思考态时补 thinking_done，
    # 否则前端思考块永远保持展开
    if content_pending:
        if in_think:
            yield {"type": "thinking_delta", "content": content_pending}
        else:
            if thinking_open:
                yield {"type": "thinking_done", "content": ""}
                thinking_open = False
            yield {"type": "text_delta", "content": content_pending}
    if in_think or thinking_open:
        yield {"type": "thinking_done", "content": ""}

    # 延迟发送tool_calls和finish（参照harness translate.ts延迟关闭模式）
    if tool_calls_buf:
        for idx, tc in tool_calls_buf.items():
            if not tc["id"]:
                tc["id"] = f"call_{idx}_{uuid.uuid4().hex[:8]}"
        yield {"type": "tool_calls", "tool_calls": list(tool_calls_buf.values())}
    yield {"type": "usage", "usage": usage or {}, "finish_reason": finish_reason}
    yield {"type": "done"}
