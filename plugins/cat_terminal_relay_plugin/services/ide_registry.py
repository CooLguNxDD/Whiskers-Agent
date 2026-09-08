"""
IdeRegistry — tracks which VS Code extension hosts are currently online and
pushes control messages to them.

Each extension dials the relay outbound on activate and holds ONE **control**
connection (``/terminal/host/{ide_id}``); the registry records
``ide_id → host entry``. The relay pushes JSON control frames over this leg
(``{"type":"open", session_id, workdir}`` / ``{"type":"kill", session_id}``); the
extension reacts by spawning/killing a ``node-pty`` and dialling a separate
per-session **data** leg (``/terminal/host/{ide_id}/session/{session_id}``) that
carries the raw PTY bytes.

One control connection serves N concurrent sessions (Phase 2 multiplexing).
"""

import asyncio
import json
import logging
import threading
import time
import weakref

from starlette.websockets import WebSocket

logger = logging.getLogger("whiskers.plugins")


class IdeRegistry:
    """In-memory map of online IDE host extensions and their control legs."""

    def __init__(self) -> None:
        """Initialize the IdeRegistry with hosts map, weakref subscribers, and thread locks."""
        # ide_id -> {"ws", "subject", "since", "session_ids": set[str]}
        self._hosts: dict[str, dict] = {}
        # Use WeakKeyDictionary so dead WS objects are auto-dropped (prevents leaks on abrupt disconnects)
        self._subscribers: weakref.WeakKeyDictionary[WebSocket, str] = weakref.WeakKeyDictionary()
        self._lock = threading.RLock()  # protects hosts + subscribers dicts for concurrent access from WS handlers / broadcasts

    async def register(self, ide_id: str, ws: WebSocket, subject: str) -> None:
        """Register a new active IDE host connection and broadcast updates."""
        with self._lock:
            self._hosts[ide_id] = {
                "ws": ws,
                "subject": subject,
                "since": time.time(),
                "session_ids": set(),
            }
            logger.info("IDE host online: %s (subject=%s)", ide_id, subject)
        # Broadcast outside lock (IO); only to the affected subject
        await self._broadcast(subject=subject)

    async def unregister(self, ide_id: str) -> None:
        """Unregister an IDE host connection by its ID and broadcast updates."""
        subject: str | None = None
        with self._lock:
            entry = self._hosts.get(ide_id)
            if entry is not None:
                subject = entry.get("subject")
                self._hosts.pop(ide_id, None)
                logger.info("IDE host offline: %s", ide_id)
        if subject is not None:
            await self._broadcast(subject=subject)
        # If subject missing, skip broadcast to avoid inadvertently notifying all subscribers.

    def is_online(self, ide_id: str) -> bool:
        """Check if an IDE host connection is currently online."""
        return ide_id in self._hosts

    def get(self, ide_id: str) -> dict | None:
        """Retrieve the host entry metadata dictionary for the given IDE ID."""
        return self._hosts.get(ide_id)

    def subscribe(self, ws: WebSocket, subject: str) -> None:
        """Subscribe a client WebSocket to receive IDE host registration updates."""
        with self._lock:
            self._subscribers[ws] = subject

    def unsubscribe(self, ws: WebSocket) -> None:
        """Unsubscribe a client WebSocket from receiving host registration updates."""
        with self._lock:
            self._subscribers.pop(ws, None)

    async def _broadcast(self, subject: str | None = None) -> None:
        # Snapshot under lock; release before awaiting IO. Concurrent sends via gather.
        items: list[tuple[WebSocket, str]]
        with self._lock:
            items = list(self._subscribers.items())

        # Build send tasks; only for matching subject (None = broadcast to all)
        send_tasks = []
        for ws, subj in items:
            if subject is not None and subj != subject:
                continue

            async def _send_one(ws: WebSocket = ws, subj: str = subj) -> None:
                try:
                    payload = json.dumps({"status": "ok", "hosts": self.list_online(subject=subj)})
                    await ws.send_text(payload)
                except Exception:
                    # mark for cleanup
                    with self._lock:
                        self._subscribers.pop(ws, None)

            send_tasks.append(_send_one())

        if send_tasks:
            await asyncio.gather(*send_tasks, return_exceptions=True)

    # -- control push --------------------------------------------------------

    async def send_control(self, ide_id: str, message: dict) -> bool:
        """Push a JSON control frame to the host's control leg. False if offline."""
        entry = self._hosts.get(ide_id)
        if entry is None:
            return False
        try:
            await entry["ws"].send_text(json.dumps(message))
            return True
        except Exception as exc:
            logger.warning("send_control failed (ide=%s): %s", ide_id, exc)
            return False

    def track_session(self, ide_id: str, session_id: str) -> None:
        """Track a terminal relay session on the specified IDE host."""
        entry = self._hosts.get(ide_id)
        if entry is not None:
            entry["session_ids"].add(session_id)

    def untrack_session(self, ide_id: str, session_id: str) -> None:
        """Untrack a terminal relay session from the specified IDE host."""
        entry = self._hosts.get(ide_id)
        if entry is not None:
            entry["session_ids"].discard(session_id)

    def list_online(self, subject: str | None = None) -> list[dict]:
        """List online hosts, optionally filtered to a subject."""
        # Snapshot to be consistent with concurrent mutations (callers may be in broadcast tasks)
        hosts_snapshot: dict[str, dict]
        # Non-async snapshot: use a cheap copy. For full safety we accept races on read for list view.
        hosts_snapshot = dict(self._hosts)
        out = []
        for ide_id, meta in hosts_snapshot.items():
            if subject is not None and meta.get("subject") != subject:
                continue
            out.append({"ide_id": ide_id, "since": meta.get("since")})
        return out
