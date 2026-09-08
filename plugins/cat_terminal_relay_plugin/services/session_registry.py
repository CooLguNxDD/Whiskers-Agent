"""
SessionRegistry — tracks live RelaySessions, enforces the per-subject cap, and
runs the TTL reaper that kills idle / over-age sessions.
"""

import asyncio
import logging
import secrets
import time

from .relay_session import RelaySession

logger = logging.getLogger("whiskers.plugins")

from plugins.cat_terminal_relay_plugin.plugin_config import SETTINGS

IDLE_TTL_SECONDS = SETTINGS.get("session_idle_ttl_seconds", 900)
ABSOLUTE_TTL_SECONDS = SETTINGS.get("session_absolute_ttl_seconds", 14400)
REAPER_INTERVAL_SECONDS = SETTINGS.get("reaper_interval_seconds", 30)
MAX_SESSIONS_PER_SUBJECT = SETTINGS.get("max_sessions_per_subject", 1)


class SessionRegistry:
    """In-memory store of session_id → RelaySession with binding + reaper."""

    def __init__(
        self,
        idle_ttl: int = IDLE_TTL_SECONDS,
        absolute_ttl: int = ABSOLUTE_TTL_SECONDS,
        max_per_subject: int = MAX_SESSIONS_PER_SUBJECT,
    ) -> None:
        """Initialize SessionRegistry with TTL and session limit configurations."""
        self._sessions: dict[str, RelaySession] = {}
        self._idle_ttl = idle_ttl
        self._absolute_ttl = absolute_ttl
        self._max_per_subject = max_per_subject
        self._reaper_task: asyncio.Task | None = None
        self._on_kill_hooks: list = []

    def add_kill_hook(self, fn) -> None:
        """Register a callback ``fn(session_id)`` invoked synchronously after a session is killed."""
        self._on_kill_hooks.append(fn)

    # -- creation / binding --------------------------------------------------

    def create(
        self, subject: str, ide_id: str, workdir: str | None = None
    ) -> RelaySession:
        """Create + register a session, enforcing the per-subject cap."""
        active = [s for s in self._sessions.values() if s.subject == subject]
        if len(active) >= self._max_per_subject:
            # Reap the oldest to honour the cap (one active session per subject).
            oldest = min(active, key=lambda s: s.created_at)
            logger.info("per-subject cap hit for %s — evicting %s",
                        subject, oldest.session_id)
            self.remove(oldest.session_id)
        session_id = secrets.token_urlsafe(16)
        session = RelaySession(session_id, subject, ide_id, workdir)
        session._on_close = self.remove
        self._sessions[session_id] = session
        logger.info("relay session created: %s (subject=%s ide=%s)",
                    session_id, subject, ide_id)
        try:
            from core.telemetry import collector
            collector.record_relay_session(
                event="open",
                subject=subject,
                ide_id=ide_id,
                session_id=session_id
            )
        except Exception:
            logger.debug("session_registry.py: swallowed exception", exc_info=True)
        return session

    def get(self, session_id: str) -> RelaySession | None:
        """Retrieve a RelaySession by its session ID, returning None if not found."""
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        """Remove a RelaySession by its session ID and log telemetry events."""
        session = self._sessions.pop(session_id, None)
        if session is not None:
            try:
                import time
                duration = time.monotonic() - session.created_at
                from core.telemetry import collector
                collector.record_relay_session(
                    event="close",
                    subject=session.subject,
                    ide_id=session.ide_id,
                    session_id=session_id,
                    bytes_total=session.bytes_total,
                    duration_s=duration
                )
            except Exception:
                logger.debug("session_registry.py: swallowed exception", exc_info=True)

    def active_count(self) -> int:
        """Number of live sessions. Public gauge surface — callers must not read ``_sessions``."""
        return len(self._sessions)

    def list_for_subject(self, subject: str) -> list[RelaySession]:
        """List all active RelaySessions associated with a given subject (username)."""
        return [s for s in self._sessions.values() if s.subject == subject]

    async def kill(self, session_id: str) -> bool:
        """Force-close a session. Returns True if it existed."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        await session.close()
        self._sessions.pop(session_id, None)
        for hook in self._on_kill_hooks:
            try:
                hook(session_id)
            except Exception as exc:
                logger.warning("kill hook error for %s: %s", session_id, exc)
        return True

    # -- reaper --------------------------------------------------------------

    def start_reaper(self) -> None:
        """Start the background session reaper task if it is not already running."""
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reap_loop())
            logger.info("session reaper started")

    async def stop_reaper(self) -> None:
        """Cancel the reaper task and await its completion so cleanup finishes."""
        task = self._reaper_task
        self._reaper_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _reap_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(REAPER_INTERVAL_SECONDS)
                await self._reap_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("reaper iteration error: %s", exc)

    async def _reap_once(self) -> None:
        now = time.monotonic()
        doomed = [
            s for s in self._sessions.values()
            if (now - s.last_activity) > self._idle_ttl
            or (now - s.created_at) > self._absolute_ttl
        ]
        for s in doomed:
            logger.info("reaping session %s (idle=%.0fs age=%.0fs)",
                        s.session_id, s.idle_seconds(), s.age_seconds())
            await self.kill(s.session_id)
