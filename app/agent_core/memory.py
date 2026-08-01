"""对话记忆管理 - 短期对话历史 + 长期决策记忆"""

import logging
import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.chat import ChatSession, ChatMessage

logger = logging.getLogger(__name__)

MAX_HISTORY_ROUNDS = 20  # 保留最近N轮对话


class ChatMemory:
    """对话记忆管理器"""

    def get_or_create_session(self, session_id: Optional[str], db: Session) -> str:
        """获取或创建会话，返回 session_id"""
        if session_id:
            existing = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
            if existing:
                return session_id

        # 创建新会话
        new_id = session_id or uuid.uuid4().hex[:16]
        session = ChatSession(session_id=new_id, title="新对话")
        db.add(session)
        db.commit()
        return new_id

    def save_message(self, session_id: str, role: str, content: Optional[str],
                     tool_calls: Optional[list] = None, tool_results: Optional[list] = None,
                     db: Session = None):
        """保存一条消息"""
        msg = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
        db.add(msg)
        db.commit()

    def get_history(self, session_id: str, db: Session, limit: int = MAX_HISTORY_ROUNDS) -> List[dict]:
        """获取对话历史（OpenAI messages 格式）"""
        messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit * 3)  # 每轮可能有 user + assistant + tool 多条
            .all()
        )
        messages.reverse()

        history = []
        for msg in messages:
            entry = {"role": msg.role, "content": msg.content or ""}
            if msg.tool_calls:
                entry["tool_calls"] = msg.tool_calls
            if msg.tool_results:
                # tool results 作为独立 tool role 消息
                for tr in msg.tool_results:
                    history.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_call_id", ""),
                        "content": tr.get("content", ""),
                    })
            history.append(entry)

        return history

    def get_all_messages(self, session_id: str, db: Session) -> List[dict]:
        """获取会话的所有消息（用于前端展示）"""
        messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        return [m.to_dict() for m in messages]

    def update_session_title(self, session_id: str, title: str, db: Session):
        """更新会话标题"""
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        if session and (not session.title or session.title == "新对话"):
            session.title = title[:50]
            db.commit()

    def list_sessions(self, db: Session, limit: int = 20) -> List[dict]:
        """列出所有会话"""
        sessions = (
            db.query(ChatSession)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "session_id": s.session_id,
                "title": s.title or "新对话",
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in sessions
        ]
