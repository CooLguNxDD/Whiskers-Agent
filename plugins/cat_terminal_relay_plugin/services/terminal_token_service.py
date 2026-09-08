"""
Layer-2 terminal token service.

A short-lived (5-min) one-time token gates opening a relay session, even when
the caller already holds a valid Layer-1 JWT. One active token per subject — a
new ``issue`` revokes the previous token for that subject. In-memory only;
expired entries are GC'd on every access. Mirror of the plan's
``TerminalTokenService``.
"""

import logging
import secrets
import time

logger = logging.getLogger("whiskers.plugins")

from plugins.cat_terminal_relay_plugin.plugin_config import SETTINGS

TOKEN_TTL_SECONDS = SETTINGS.get("terminal_token_ttl_seconds", 300)


class TerminalTokenService:
    """In-memory one-per-subject TTL token store for terminal session opens."""

    def __init__(self, ttl_seconds: int = TOKEN_TTL_SECONDS) -> None:
        """Initialize the TerminalTokenService with a target TTL and empty tracking maps."""
        self._ttl = ttl_seconds
        # token -> (subject, expires_at)
        self._tokens: dict[str, tuple[str, float]] = {}
        # subject -> token, so a fresh issue revokes the prior one
        self._by_subject: dict[str, str] = {}

    def _gc(self) -> None:
        """Drop expired tokens (called on every access)."""
        now = time.monotonic()
        expired = [t for t, (_, exp) in self._tokens.items() if exp <= now]
        for t in expired:
            subject, _ = self._tokens.pop(t)
            if self._by_subject.get(subject) == t:
                self._by_subject.pop(subject, None)

    def issue(self, subject: str) -> tuple[str, int]:
        """Issue a token for ``subject``, revoking any prior token. Returns (token, ttl)."""
        self._gc()
        # Revoke prior token for this subject.
        old = self._by_subject.pop(subject, None)
        if old:
            self._tokens.pop(old, None)
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (subject, time.monotonic() + self._ttl)
        self._by_subject[subject] = token
        logger.info("terminal token issued for subject=%s", subject)
        return token, self._ttl

    def consume(self, token: str) -> str | None:
        """Validate + single-use consume a token. Returns the subject, or None."""
        self._gc()
        entry = self._tokens.pop(token, None)
        if entry is None:
            return None
        subject, exp = entry
        if self._by_subject.get(subject) == token:
            self._by_subject.pop(subject, None)
        if exp <= time.monotonic():
            return None
        return subject

    def peek(self, token: str) -> str | None:
        """Non-consuming validity check. Returns subject or None."""
        self._gc()
        entry = self._tokens.get(token)
        if entry is None:
            return None
        subject, exp = entry
        return subject if exp > time.monotonic() else None
