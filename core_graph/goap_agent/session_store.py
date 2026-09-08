"""In-process GoapAgent session store (MCP node surface)."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GoapAgentSession:
    """One interactive graph session for stepwise node invocation."""

    session_id: str
    state: dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)
    last_node: str | None = None
    history: list[str] = field(default_factory=list)


class SessionStore:
    """Thread-safe in-memory session map with optional TTL eviction."""

    def __init__(self, *, max_sessions: int = 128, ttl_s: float = 3600.0) -> None:
        self._sessions: dict[str, GoapAgentSession] = {}
        self._lock = asyncio.Lock()
        self._max_sessions = max(1, max_sessions)
        self._ttl_s = max(60.0, ttl_s)

    async def create(
        self,
        state: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> GoapAgentSession:
        """Create and store a new session."""
        sid = (session_id or "").strip() or f"goap-agent-{uuid.uuid4().hex[:16]}"
        async with self._lock:
            self._evict_locked()
            if len(self._sessions) >= self._max_sessions and sid not in self._sessions:
                # Drop oldest
                oldest = min(self._sessions.values(), key=lambda s: s.updated_at)
                self._sessions.pop(oldest.session_id, None)
            sess = GoapAgentSession(session_id=sid, state=dict(state))
            sess.state["session_id"] = sid
            self._sessions[sid] = sess
            return sess

    async def get(self, session_id: str) -> GoapAgentSession | None:
        """Return a session or None."""
        async with self._lock:
            self._evict_locked()
            return self._sessions.get(session_id)

    async def update(
        self,
        session_id: str,
        state: dict[str, Any],
        *,
        last_node: str | None = None,
    ) -> GoapAgentSession | None:
        """Replace session state; record last node name."""
        async with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return None
            sess.state = dict(state)
            sess.updated_at = time.monotonic()
            if last_node:
                sess.last_node = last_node
                sess.history.append(last_node)
                if len(sess.history) > 200:
                    sess.history = sess.history[-200:]
            return sess

    async def destroy(self, session_id: str) -> bool:
        """Remove a session. Returns True if it existed."""
        async with self._lock:
            return self._sessions.pop(session_id, None) is not None

    async def list_ids(self) -> list[str]:
        """Return active session ids."""
        async with self._lock:
            self._evict_locked()
            return list(self._sessions.keys())

    def _evict_locked(self) -> None:
        now = time.monotonic()
        expired = [
            sid for sid, s in self._sessions.items()
            if (now - s.updated_at) > self._ttl_s
        ]
        for sid in expired:
            self._sessions.pop(sid, None)


_store: SessionStore | None = None
_store_lock = asyncio.Lock()


async def get_session_store() -> SessionStore:
    """Return the process-wide SessionStore singleton."""
    global _store
    if _store is not None:
        return _store
    async with _store_lock:
        if _store is None:
            _store = SessionStore()
        return _store


def reset_session_store_for_tests() -> None:
    """Drop the singleton (unit tests only)."""
    global _store
    _store = None
