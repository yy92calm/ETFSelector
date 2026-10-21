"""权限引擎 - 决定工具调用放行/询问/拒绝

精简移植 OpenWorker permissions.py：读写二元分类 + 三模式 + 会话级放行清单。
引擎只做「允许/拒绝/需询问」判定，等待与结果回填由 AgentLoop 处理。
"""

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Set


class Mode(str, Enum):
    DISCUSS = "discuss"  # 只读问答：写操作一律拒绝
    INTERACTIVE = "interactive"  # 默认：读自动、写问用户
    AUTO = "auto"  # 全放行（预留给未来自动化场景）


READ_ONLY_MODES = frozenset({Mode.DISCUSS})

# 进程级会话授权清单：AgentLoop 每个请求新建实例，「本会话允许」必须跨实例共享
_SESSION_ALLOW: Dict[str, Set[str]] = {}
_SESSION_ALLOW_LOCK = threading.Lock()


def remember_session_allow(session_id: str, tool_name: str) -> None:
    """记录某会话已放行的工具（进程级，单进程部署假设与 ApprovalStore 一致）"""
    with _SESSION_ALLOW_LOCK:
        _SESSION_ALLOW.setdefault(session_id, set()).add(tool_name)


def is_session_tool_allowed(session_id: Optional[str], tool_name: str) -> bool:
    if not session_id:
        return False
    with _SESSION_ALLOW_LOCK:
        return tool_name in _SESSION_ALLOW.get(session_id, ())


def clear_session_permissions(session_id: str) -> None:
    """会话删除时清理授权，避免 session_id 复用时继承旧授权"""
    if not session_id:
        return
    with _SESSION_ALLOW_LOCK:
        _SESSION_ALLOW.pop(session_id, None)


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    needs_user: bool = False  # True → 前端应弹出审批卡片


@dataclass
class PermissionEngine:
    mode: Mode = Mode.INTERACTIVE
    session_allow_tools: Set[str] = field(default_factory=set)

    def evaluate(self, tool_name: str, risk_level: str = "read",
                 session_id: Optional[str] = None) -> Decision:
        is_write = risk_level == "write"

        # 只读模式拒绝写操作
        if self.mode in READ_ONLY_MODES and is_write:
            return Decision(False, "当前为只读问答模式，写操作已拒绝")

        # 非写操作一律放行
        if not is_write:
            return Decision(True, "只读操作")

        # 本会话已放行（实例清单 + 进程级共享清单）
        if tool_name in self.session_allow_tools or is_session_tool_allowed(session_id, tool_name):
            return Decision(True, "本会话已放行")

        # 全自动模式
        if self.mode is Mode.AUTO:
            return Decision(True, "全自动模式")

        # 默认：需要用户审批
        return Decision(False, "写操作需审批", needs_user=True)

    def allow_tool_for_session(self, tool_name: str, session_id: Optional[str] = None) -> None:
        self.session_allow_tools.add(tool_name)
        if session_id:
            remember_session_allow(session_id, tool_name)
