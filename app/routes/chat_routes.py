"""对话式交互 API"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["AI对话"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="用户消息")
    session_id: Optional[str] = Field(None, description="会话ID，不传则新建")


@router.post("", response_model=APIResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)):
    """对话接口 - 发送消息给AI，返回回复+工具执行结果"""
    from app.agent_core.loop import AgentLoop

    agent = AgentLoop()
    result = agent.run(request.message, request.session_id, db)

    return APIResponse(data={
        "session_id": result.session_id,
        "reply": result.content,
        "tool_calls": result.tool_calls_made,
        "error": result.error,
    })


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
