"""上下文压缩 - LLM 摘要 + 只改出站视图

精简移植 OpenWorker compaction.py：
- canonical 历史（ChatMessage 行）永不被修改
- 出站视图 = 摘要块 + 最近若干轮消息，旧消息不再发送
- 摘要失败自动降级为 trim，绝不让对话卡在压缩上
"""

import logging
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# 压缩后出站视图保留的最近轮数
RECENT_ROUNDS_AFTER_COMPACTION = 8

_COMPACTION_PROMPT = """请将以下对话历史压缩为结构化摘要，用于后续对话继续：
1. 保留：用户全部请求与意图、关键决策及其原因(WHY)、已执行的操作、当前策略/持仓状态、待办
2. 只记录"涉及哪个对象/主题"，不要保留大段原文，需要细节时工具会重新查询
3. 要点式，不超过300字"""


def estimate_tokens(messages: List[dict]) -> int:
    """粗略估算 token 数（中英文混合按 ~4 字符/token 计，不引入 tiktoken）"""
    total_chars = sum(len(m.get("content") or "") for m in messages)
    return total_chars // 4


def compaction_due(signal_tokens: int, context_window_tokens: int,
                   threshold: float, min_tokens: int) -> bool:
    """是否触发压缩：signal >= min(threshold × window, ...) 且不低于 min_tokens"""
    if signal_tokens < min_tokens:
        return False
    return signal_tokens >= context_window_tokens * threshold


def _messages_to_text(messages: List[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def summarize(messages: List[dict], complete: Callable[[List[dict]], str]) -> str:
    """LLM 摘要压缩；失败降级为 trim 最近消息

    Args:
        messages: 待压缩的旧消息（OpenAI messages 格式）
        complete: 调用 LLM 并返回文本的可回调（由调用方注入，保持本模块无依赖）

    Returns:
        摘要文本
    """
    if not messages:
        return ""
    try:
        summary = complete([
            {"role": "system", "content": "你是对话摘要器，输出简洁要点。"},
            {"role": "user", "content": _COMPACTION_PROMPT + "\n\n对话历史:\n" + _messages_to_text(messages)},
        ])
        return (summary or "").strip()
    except Exception as e:
        logger.error(f"LLM 摘要压缩失败，降级 trim: {e}")
        return _trim_fallback(messages)


def _trim_fallback(messages: List[dict]) -> str:
    """降级方案：机械保留用户消息逐字 + 最近动作，其余丢弃"""
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    kept = user_msgs[-10:]
    body = "\n".join(f"- 用户: {u[:100]}" for u in kept if u)
    return f"（历史过长，已截断。最近用户请求：）\n{body}"
