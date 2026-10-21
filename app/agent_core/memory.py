"""对话记忆管理 - 短期对话历史 + 长期决策记忆"""

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.chat import ChatSession, ChatMessage
from app.agent_core.compaction import RECENT_ROUNDS_AFTER_COMPACTION

logger = logging.getLogger(__name__)

MAX_HISTORY_ROUNDS = 20  # 保留最近N轮对话


def _rebuild_history(messages: List[ChatMessage]) -> List[dict]:
    """把 canonical 消息行重建为 OpenAI messages（assistant 在前、tool 紧随其后）

    工具结果与工具调用存在同一行；没有配套结果的 tool_call 剔除，
    窗口头部孤立的 tool 消息丢弃，保证出站序列不违反 tool_calls 协议。
    """
    history = []
    for msg in messages:
        entry = {"role": msg.role, "content": msg.content or ""}
        calls = msg.tool_calls or []
        results = msg.tool_results or []
        result_ids = {tr.get("tool_call_id") for tr in results if tr.get("tool_call_id")}
        paired = [tc for tc in calls
                  if isinstance(tc, dict) and tc.get("id") in result_ids]
        if calls and not paired:
            logger.warning(f"历史消息 {msg.id} 的 tool_calls 无配套结果，已从出站视图剔除")
        if paired:
            entry["tool_calls"] = paired
            history.append(entry)
            paired_ids = {tc["id"] for tc in paired}
            for tr in results:
                if tr.get("tool_call_id") in paired_ids:
                    history.append({
                        "role": "tool",
                        "tool_call_id": tr["tool_call_id"],
                        "content": tr.get("content", ""),
                    })
        else:
            history.append(entry)

    while history and history[0]["role"] == "tool":
        history.pop(0)
    return history


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
                     usage: Optional[dict] = None, db: Session = None) -> int:
        """保存一条消息，返回消息 id（调用方据此从历史中精确排除）"""
        msg = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_results=tool_results,
            usage=usage,
        )
        db.add(msg)
        # 会话列表按最近活动排序：只插消息不会触发 ChatSession 的 onupdate
        db.query(ChatSession).filter(ChatSession.session_id == session_id).update(
            {"updated_at": datetime.utcnow()}, synchronize_session=False
        )
        db.commit()
        return msg.id

    def get_history(self, session_id: str, db: Session, limit: int = MAX_HISTORY_ROUNDS,
                    exclude_message_id: Optional[int] = None) -> List[dict]:
        """获取对话历史（OpenAI messages 格式，只含真实消息）

        压缩摘要不在此注入：由 loop 拼入每轮的「系统状态快照」user 消息，
        避免消息列表中段出现 system role（部分 provider 不接受）。
        有摘要时出站视图只保留最近若干轮，canonical 历史不变。
        exclude_message_id 按主键排除单条消息（当前刚写入的用户消息），
        比「内容相同就丢弃末条」可靠——用户重复问同一句话时不会误删上一条历史。

        出站序列必须满足 OpenAI 协议：assistant(tool_calls) 在前、对应 tool 结果紧随其后。
        工具结果与工具调用存在同一行，重建时先发 assistant 再发 tool；
        没有配套结果的 tool_call 直接剔除（中断/异常轮次），不留下孤儿。
        """
        if self.get_summary(session_id, db):
            # 压缩后只保留最近若干轮，避免超长（轮数与压缩模块的出站视图口径同源）
            limit = min(limit, RECENT_ROUNDS_AFTER_COMPACTION)

        query = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
        if exclude_message_id:
            query = query.filter(ChatMessage.id != exclude_message_id)
        # 按自增主键排序：同一秒写入的多条消息用 created_at 会得到不稳定顺序
        messages = query.order_by(ChatMessage.id.desc()) \
            .limit(limit * 3)  # 每轮可能有 user + assistant + tool 多条
        messages = messages.all()
        messages.reverse()

        return _rebuild_history(messages)

    def get_summary(self, session_id: str, db: Session) -> str:
        """获取会话的上下文压缩摘要"""
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        return (session.context_summary or "") if session else ""

    def save_summary(self, session_id: str, summary: str, db: Session):
        """保存会话的上下文压缩摘要"""
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        if session:
            session.context_summary = summary
            db.commit()

    def get_session_model(self, session_id: str, db: Session) -> str:
        """获取会话级模型（空表示用默认模型）"""
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        return (session.model or "") if session else ""

    def set_session_model(self, session_id: str, model: str, db: Session):
        """设置会话级模型"""
        session = db.query(ChatSession).filter(ChatSession.session_id == session_id).first()
        if session:
            session.model = model or None
            db.commit()

    def get_all_messages(self, session_id: str, db: Session) -> List[dict]:
        """获取会话的所有消息（用于前端展示）"""
        messages = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id.asc())
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

    def delete_session(self, session_id: str, db: Session) -> bool:
        """删除会话及其所有消息"""
        db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
        result = db.query(ChatSession).filter(ChatSession.session_id == session_id).delete()
        db.commit()
        return result > 0
