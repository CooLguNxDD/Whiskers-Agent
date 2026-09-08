"""
Database persistence store for playground chat sessions and messages.
"""

import logging
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy import select, update
from db_layer.connection import get_async_session
from db_layer.models import ChatSession, ChatMessage

logger = logging.getLogger("whiskers")


async def create_session(title: str) -> str:
    """Creates a ChatSession row. Returns str(session.id)."""
    async with get_async_session() as session:
        chat_session = ChatSession(title=title[:200])
        session.add(chat_session)
        await session.flush()
        session_id = str(chat_session.id)
        await session.commit()
        return session_id


async def append_message(
    session_id: str,
    role: str,
    content: str,
    engine: str | None = None,
    goap_state: dict | None = None,
    raw: dict | None = None,
) -> str:
    """Appends a ChatMessage to a session. Returns str(message.id)."""
    try:
        uuid_val = UUID(session_id)
    except ValueError:
        logger.warning("append_message: Invalid session_id '%s'", session_id)
        raise

    async with get_async_session() as session:
        msg = ChatMessage(
            session_id=uuid_val,
            role=role,
            content=content,
            engine=engine,
            goap_state=goap_state,
            raw=raw,
        )
        session.add(msg)
        await session.flush()
        msg_id = str(msg.id)
        await session.commit()
        return msg_id


async def list_sessions(limit: int = 50) -> list[dict]:
    """Returns all chat sessions, newest first (limited by default to 50 for performance)."""
    limit = max(0, limit)
    async with get_async_session() as session:
        stmt = select(ChatSession).order_by(ChatSession.updated_at.desc()).limit(limit)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "title": r.title,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]


async def get_session(session_id: str) -> dict | None:
    """Returns a session dict with its messages. Returns None if not found."""
    try:
        uuid_val = UUID(session_id)
    except ValueError:
        return None

    async with get_async_session() as db:
        stmt = (
            select(ChatSession, ChatMessage)
            .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
            .where(ChatSession.id == uuid_val)
            .order_by(ChatMessage.created_at.asc())
        )
        result = await db.execute(stmt)
        rows = result.all()

        if not rows:
            return None

        # Parent session is duplicated on each joined row; take first.
        chat_session = rows[0][0]
        # Outer join yields one null-message row for empty sessions.
        messages = [row[1] for row in rows if row[1] is not None]

        return {
            "id": str(chat_session.id),
            "title": chat_session.title,
            "created_at": chat_session.created_at.isoformat() if chat_session.created_at else None,
            "updated_at": chat_session.updated_at.isoformat() if chat_session.updated_at else None,
            "messages": [
                {
                    "id": str(m.id),
                    "role": m.role,
                    "content": m.content,
                    "engine": m.engine,
                    "goap_state": m.goap_state,
                    "raw": m.raw,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in messages
            ],
        }


async def touch_session(session_id: str) -> None:
    """Bumps updated_at on a session to mark recent activity."""
    try:
        uuid_val = UUID(session_id)
    except ValueError:
        logger.warning("touch_session: Invalid session_id '%s'", session_id)
        return

    async with get_async_session() as session:
        stmt = (
            update(ChatSession)
            .where(ChatSession.id == uuid_val)
            .values(updated_at=datetime.now(timezone.utc))
        )
        await session.execute(stmt)
        await session.commit()
