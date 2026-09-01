"""对话与AI决策日志模型"""

from sqlalchemy import Column, String, Integer, Text, DateTime, JSON
from datetime import datetime
from app.db.database import Base


class ChatSession(Base):
    """对话会话"""
    __tablename__ = "chat_session"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, unique=True, index=True, comment="会话唯一标识")
    title = Column(String(200), nullable=True, comment="会话标题（自动生成）")
    context_summary = Column(Text, nullable=True, comment="上下文压缩摘要（只用于出站视图）")
    model = Column(String(100), nullable=True, comment="会话级模型（覆盖默认模型，空则用默认）")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<ChatSession {self.session_id}>"


class ChatMessage(Base):
    """对话消息"""
    __tablename__ = "chat_message"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True, comment="所属会话")
    role = Column(String(20), nullable=False, comment="user / assistant / tool")
    content = Column(Text, nullable=True, comment="消息文本内容")
    tool_calls = Column(JSON, nullable=True, comment="AI发起的工具调用列表")
    tool_results = Column(JSON, nullable=True, comment="工具执行结果列表")
    usage = Column(JSON, nullable=True, comment="本轮LLM用量（prompt/completion/total tokens与finish_reason）")
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ChatMessage {self.id} [{self.role}]>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "usage": self.usage,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AIActionLog(Base):
    """AI自主决策日志（区别于对话触发的操作）"""
    __tablename__ = "ai_action_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trigger_type = Column(String(30), nullable=False, comment="触发类型: daily/condition/manual")
    reasoning = Column(Text, nullable=True, comment="AI推理过程摘要")
    actions_taken = Column(JSON, nullable=True, comment="执行的操作列表")
    tools_called = Column(JSON, nullable=True, comment="调用的工具及参数")
    status = Column(String(20), nullable=False, default="completed", comment="completed/pending_approval/failed")
    approval_status = Column(String(20), nullable=True, comment="审批状态: approved/rejected/pending")
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<AIActionLog {self.id} [{self.trigger_type}] {self.status}>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "trigger_type": self.trigger_type,
            "reasoning": self.reasoning,
            "actions_taken": self.actions_taken,
            "tools_called": self.tools_called,
            "status": self.status,
            "approval_status": self.approval_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
