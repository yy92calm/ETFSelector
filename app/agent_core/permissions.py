"""权限引擎 - 决定工具调用放行/询问/拒绝

精简移植 OpenWorker permissions.py：读写二元分类 + 三模式 + 会话级放行清单。
引擎只做「允许/拒绝/需询问」判定，等待与结果回填由 AgentLoop 处理。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Set


class Mode(str, Enum):
    DISCUSS = "discuss"  # 只读问答：写操作一律拒绝
    INTERACTIVE = "interactive"  # 默认：读自动、写问用户
    AUTO = "auto"  # 全放行（预留给未来自动化场景）


READ_ONLY_MODES = frozenset({Mode.DISCUSS})


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    needs_user: bool = False  # True → 前端应弹出审批卡片


@dataclass
class PermissionEngine:
    mode: Mode = Mode.INTERACTIVE
    session_allow_tools: Set[str] = field(default_factory=set)

    def evaluate(self, tool_name: str, risk_level: str = "read") -> Decision:
        is_write = risk_level == "write"

        # 只读模式拒绝写操作
        if self.mode in READ_ONLY_MODES and is_write:
            return Decision(False, "当前为只读问答模式，写操作已拒绝")

        # 非写操作一律放行
        if not is_write:
            return Decision(True, "只读操作")

        # 本会话已放行
        if tool_name in self.session_allow_tools:
            return Decision(True, "本会话已放行")

        # 全自动模式
        if self.mode is Mode.AUTO:
            return Decision(True, "全自动模式")

        # 默认：需要用户审批
        return Decision(False, "写操作需审批", needs_user=True)

    def allow_tool_for_session(self, tool_name: str) -> None:
        self.session_allow_tools.add(tool_name)
