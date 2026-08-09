"""对话式交互 API"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["AI对话"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="用户消息")
    session_id: Optional[str] = Field(None, description="会话ID，不传则新建")


class ApproveRequest(BaseModel):
    request_id: str = Field(..., description="审批请求ID")
    outcome: str = Field(..., description="once/always/deny")


class StopRequest(BaseModel):
    session_id: str = Field(..., description="会话ID")


class ModelRequest(BaseModel):
    session_id: str = Field(..., description="会话ID")
    model: str = Field(..., min_length=1, max_length=100, description="会话级模型名（空串恢复默认）")


@router.post("", response_model=APIResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    """对话接口 - 发送消息给AI，返回回复+工具执行结果
    
    注意：非流式接口写操作直接执行（AUTO模式，无审批），流式接口 /api/chat/stream 支持审批（INTERACTIVE模式）
    """
    from app.agent_core.loop import AgentLoop
    from app.agent_core.permissions import Mode

    agent = AgentLoop()
    agent.permissions.mode = Mode.AUTO  # 非流式接口写操作直接执行
    result = agent.run(request.message, request.session_id, db)

    return APIResponse(data={
        "session_id": result.session_id,
        "reply": result.content,
        "tool_calls": result.tool_calls_made,
        "error": result.error,
    })


@router.post("/approve", response_model=APIResponse)
def approve_request(request: ApproveRequest, db: Session = Depends(get_db)):
    """审批接口 - 响应流式对话中写操作的审批请求"""
    from app.agent_core.approvals import get_approval_store

    ok = get_approval_store().resolve(request.request_id, request.outcome)
    if not ok:
        return APIResponse(code=404, message="审批请求不存在或已超时", data=None)
    return APIResponse(data={"request_id": request.request_id, "outcome": request.outcome})


@router.post("/stop", response_model=APIResponse)
def stop_conversation(request: StopRequest):
    """中断接口 - 停止正在运行的对话 turn（边界生效）"""
    from app.agent_core.loop import request_interrupt

    request_interrupt(request.session_id)
    return APIResponse(data={"session_id": request.session_id})


@router.post("/model", response_model=APIResponse)
def switch_model(request: ModelRequest, db: Session = Depends(get_db)):
    """切换会话级模型 - 命中 llm_model_aliases 前缀则切换 Provider，空串恢复默认"""
    from app.agent_core.memory import ChatMemory
    from app.agent_core.provider import resolve_llm

    memory = ChatMemory()
    model = request.model.strip()
    memory.set_session_model(request.session_id, model, db)

    base_url, api_key, effective = resolve_llm(model) if model else (None, None, "")
    return APIResponse(data={
        "session_id": request.session_id,
        "model": model or "(默认)",
        "effective_model": effective,
        "provider": base_url or "default",
    })


@router.get("/model/options", response_model=APIResponse)
def model_options():
    """列出可选会话模型（默认模型 + llm_model_aliases 声明的模型前缀）"""
    from app.config import get_settings
    from app.agent_core.provider import parse_aliases

    settings = get_settings()
    prefixes = list(parse_aliases().keys())
    return APIResponse(data={
        "default": settings.llm_model,
        "models": [settings.llm_model] + prefixes,
    })


@router.post("/stream")
def chat_stream(request: ChatRequest, db: Session = Depends(get_db)):
    """对话流式接口（SSE）- 实时推送工具调用过程与最终回复"""
    from app.agent_core.loop import AgentLoop

    agent = AgentLoop()

    def event_stream():
        for ev in agent.run_streaming(request.message, request.session_id, db):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/history", response_model=APIResponse)
def get_chat_history(session_id: str, db: Session = Depends(get_db)):
    """获取指定会话的对话历史"""
    from app.agent_core.memory import ChatMemory

    memory = ChatMemory()
    messages = memory.get_all_messages(session_id, db)

    return APIResponse(data={
        "session_id": session_id,
        "messages": messages,
        "total": len(messages),
    })


@router.get("/sessions", response_model=APIResponse)
def list_sessions(db: Session = Depends(get_db)):
    """列出所有对话会话"""
    from app.agent_core.memory import ChatMemory

    memory = ChatMemory()
    sessions = memory.list_sessions(db)

    return APIResponse(data={"sessions": sessions})


@router.get("/tools", response_model=APIResponse)
def list_tools():
    """列出所有可用的AI工具"""
    from app.tools.registry import get_tool_registry

    registry = get_tool_registry()
    tools = registry.get_openai_tools()

    return APIResponse(data={
        "total": len(tools),
        "tools": [{"name": t["function"]["name"], "description": t["function"]["description"]} for t in tools],
    })
