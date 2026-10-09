"""上下文压缩 - LLM 摘要 + 只改出站视图

精简移植 OpenWorker compaction.py：
- canonical 历史（ChatMessage 行）永不被修改
- 出站视图 = 摘要块 + 最近若干轮消息，旧消息不再发送
- 摘要失败自动降级为 trim，绝不让对话卡在压缩上
- 分层触发：轻度只摘要最早的一半（近因保留原文），重度全量摘要
"""

import logging
import re
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# 压缩后出站视图保留的最近轮数
RECENT_ROUNDS_AFTER_COMPACTION = 8

# token 估算：中文约 1.5 字/token，英文数字标点约 4 字符/token
# 一律按 4 字符估会把中文会话低估 2 倍以上，导致「还没压」就撞上下文上限
CJK_CHARS_PER_TOKEN = 1.5
OTHER_CHARS_PER_TOKEN = 4.0

# 摘要长度预算（字）：随原文规模伸缩，小对话不必写 600 字，大对话不能只写 200 字
SUMMARY_BUDGET_MIN = 200
SUMMARY_BUDGET_MAX = 600

# trim 降级时保留的最近消息条数
TRIM_KEEP_USER_MESSAGES = 10
TRIM_KEEP_ASSISTANT_MESSAGES = 4

_CJK_RANGES = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

_COMPACTION_PROMPT = """请将以下对话历史压缩为结构化摘要，用于后续对话继续：
1. 保留：用户全部请求与意图、关键决策及其原因(WHY)、已执行的操作、当前策略/持仓状态、待办
2. 只记录"涉及哪个对象/主题"，不要保留大段原文，需要细节时工具会重新查询
3. 要点式，不超过{budget}字"""


def _text_tokens(text: str) -> float:
    """按字符类型分别折算 token（不引入 tiktoken）"""
    if not text:
        return 0.0
    cjk = len(_CJK_RANGES.findall(text))
    return cjk / CJK_CHARS_PER_TOKEN + (len(text) - cjk) / OTHER_CHARS_PER_TOKEN


def estimate_tokens(messages: List[dict]) -> int:
    """估算出站 messages 的 token 数

    tool_calls 的参数也要计费：一轮 ReAct 的 JSON arguments 常常比正文还长，
    漏算会让估算系统性偏低，压缩永远不触发。
    """
    total = 0.0
    for m in messages:
        total += _text_tokens(m.get("content") or "")
        for call in (m.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            total += _text_tokens(function.get("name") or "")
            total += _text_tokens(function.get("arguments") or "")
    return int(total)


def compaction_tier(signal_tokens: int, context_window_tokens: int, min_tokens: int,
                   light_ratio: float, heavy_ratio: float) -> Optional[str]:
    """应执行的压缩档位：light（只摘要最早一半）/ heavy（全量摘要）/ None（暂不压缩）

    分层的意义是成本：轻度只花半份摘要 token，且近期消息仍以原文呈现；
    逼近上限时才全量摘要换取最大空间。
    """
    if signal_tokens < min_tokens or context_window_tokens <= 0:
        return None
    ratio = signal_tokens / context_window_tokens
    if ratio >= heavy_ratio:
        return "heavy"
    if ratio >= light_ratio:
        return "light"
    return None


def summary_budget(text: str) -> int:
    """摘要长度上限：按原文规模伸缩"""
    return min(SUMMARY_BUDGET_MAX, max(SUMMARY_BUDGET_MIN, len(text) // 5))


def _messages_to_text(messages: List[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def summarize(messages: List[dict], complete: Callable[[List[dict]], str],
              tier: str = "heavy", prior_summary: str = "") -> str:
    """LLM 摘要压缩；失败降级为 trim 最近消息

    Args:
        messages: 待压缩的旧消息（OpenAI messages 格式）
        complete: 调用 LLM 并返回文本的可回调（由调用方注入，保持本模块无依赖）
        tier: light 只摘要最早一半消息（近因保持原文），heavy 全量摘要
        prior_summary: 上一次压缩的摘要；新摘要在其上续写，
            否则压缩次数越多丢的越多（旧摘要会被整份替换）

    Returns:
        摘要文本
    """
    if not messages:
        return ""
    if tier == "light":
        source = messages[:len(messages) // 2]
        if not source:
            return ""
    else:
        source = messages
    body = _messages_to_text(source)
    if prior_summary:
        body = "[已有摘要]\n" + prior_summary + "\n\n[需并入的新对话]\n" + body
    try:
        summary = complete([
            {"role": "system", "content": "你是对话摘要器，输出简洁要点。"},
            {"role": "user", "content": _COMPACTION_PROMPT.format(budget=summary_budget(body))
             + "\n\n对话历史:\n" + body},
        ])
        return (summary or "").strip()
    except Exception as e:
        logger.error(f"LLM 摘要压缩失败，降级 trim: {e}")
        fallback = _trim_fallback(source)
        return (prior_summary + "\n" + fallback).strip() if prior_summary else fallback


def _trim_fallback(messages: List[dict]) -> str:
    """降级方案：机械保留用户消息逐字 + 最近助手动作，其余丢弃"""
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    # 助手消息里是推理过程与工具结论（查过什么、得出什么），全丢会让 LLM 重复劳动
    assistant_msgs = [m.get("content", "") for m in messages if m.get("role") == "assistant"]

    kept = user_msgs[-TRIM_KEEP_USER_MESSAGES:]
    body = "\n".join(f"- 用户: {u[:100]}" for u in kept if u)
    actions = "\n".join(f"- 助手: {a[:100]}" for a in assistant_msgs[-TRIM_KEEP_ASSISTANT_MESSAGES:]
                        if a)
    parts = ["（历史过长，已截断。最近用户请求：）", body]
    if actions:
        parts.append("最近的推理与结论：")
        parts.append(actions)
    return "\n".join(p for p in parts if p)
