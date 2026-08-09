"""审批待办存储 - SSE 事件流与 POST /api/chat/approve 之间的进程内桥接

AgentLoop（SSE 生成器，跑在 threadpool 线程）对写工具创建 PendingApproval，
yield permission_required 事件后阻塞等待；前端收到事件后调 /api/chat/approve，
在另一线程 resolve() 唤醒等待线程。超时自动 deny。
"""

import threading
import time
import uuid
from typing import Dict, Optional

APPROVAL_OUTCOME_ONCE = "once"
APPROVAL_OUTCOME_ALWAYS = "always"
APPROVAL_OUTCOME_DENY = "deny"


class PendingApproval:
    def __init__(self, tool: str, arguments: dict, timeout: int = 120):
        self.request_id = uuid.uuid4().hex[:16]
        self.tool = tool
        self.arguments = arguments
        self.created_at = time.time()
        self.timeout = timeout
        self.event = threading.Event()
        self.outcome: Optional[str] = None  # once / always / deny


class ApprovalStore:
    """进程内审批待办表，线程安全"""

    def __init__(self):
        self._pending: Dict[str, PendingApproval] = {}
        self._lock = threading.Lock()
        self._auto_approve: bool = False

    @property
    def auto_approve(self) -> bool:
        return self._auto_approve

    @auto_approve.setter
    def auto_approve(self, value: bool):
        self._auto_approve = value

    def create(self, tool: str, arguments: dict, timeout: int = 120) -> PendingApproval:
        req = PendingApproval(tool, arguments, timeout)
        with self._lock:
            self._pending[req.request_id] = req
        return req

    def resolve(self, request_id: str, outcome: str) -> bool:
        """按 request_id 完成一次审批，返回是否找到该待办"""
        with self._lock:
            req = self._pending.pop(request_id, None)
        if not req:
            return False
        req.outcome = outcome
        req.event.set()
        return True

    def wait(self, req: PendingApproval) -> str:
        """阻塞等待审批结果，超时返回 deny"""
        if not req.event.wait(req.timeout):
            with self._lock:
                self._pending.pop(req.request_id, None)
            req.outcome = APPROVAL_OUTCOME_DENY
            return APPROVAL_OUTCOME_DENY
        return req.outcome or APPROVAL_OUTCOME_DENY


_store: Optional[ApprovalStore] = None


def get_approval_store() -> ApprovalStore:
    global _store
    if _store is None:
        _store = ApprovalStore()
    return _store
