"""
ElevationService — manages Layer-2 terminal capability elevation tokens.
"""

import logging
import secrets
import time
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

logger = logging.getLogger("whiskers.plugins")


class ElevationService:
    """Manages session-level capability tokens and factor verification."""

    def __init__(
        self,
        method: str = "totp",
        ttl_seconds: int = 300,
        max_attempts: int = 3,
        lockout_seconds: int = 300,
        plugin_id: str = "cat_terminal_relay_plugin",
        vault=None,
    ) -> None:
        """Initialize variables and configuration parameters."""
        self.method = method
        self.ttl_seconds = ttl_seconds
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self.plugin_id = plugin_id

        if vault is None:
            from db_layer.vault import VaultService
            self.vault = VaultService()
        else:
            self.vault = vault

        # token -> (session_id, subject, expires_at)
        self._tokens: dict[str, tuple[str, str, float]] = {}
        # session_id -> token (re-elevation revokes prior)
        self._by_session: dict[str, str] = {}
        # session_id -> (count, locked_until)
        self._attempts: dict[str, tuple[int, float]] = {}
        # subject -> {(code, time_step)} consumed
        self._replay: dict[str, set[tuple[str, int]]] = {}

    def _gc(self) -> None:
        """Drop expired elevation tokens."""
        now = time.time()
        expired = [t for t, (_, _, exp) in self._tokens.items() if exp <= now]
        for t in expired:
            entry = self._tokens.pop(t, None)
            if entry:
                session_id, _, _ = entry
                if self._by_session.get(session_id) == t:
                    self._by_session.pop(session_id, None)

    def _record_failure(self, session_id: str) -> tuple[int, float | None]:
        """Increment failure count for session and apply lockout if max attempts exceeded."""
        count, locked_until = self._attempts.get(session_id, (0, 0.0))
        now = time.time()

        # Reset count if lockout expired
        if locked_until > 0 and locked_until <= now:
            count = 0
            locked_until = 0.0

        count += 1
        if count >= self.max_attempts:
            locked_until = now + self.lockout_seconds
            self._attempts[session_id] = (count, locked_until)
            return count, locked_until
        else:
            self._attempts[session_id] = (count, 0.0)
            return count, None

    async def verify_and_mint(
        self,
        session_id: str,
        subject: str,
        totp: str | None = None,
        password: str | None = None,
    ) -> dict:
        """Verify the configured factor(s); on success mint+store a capability token."""
        now = time.time()
        self._gc()

        # Check lockout status
        if session_id in self._attempts:
            count, locked_until = self._attempts[session_id]
            if locked_until > now:
                return {
                    "status": "error",
                    "error": "locked",
                    "message": "Too many failed elevation attempts. Session locked.",
                    "locked_until": locked_until,
                }
            elif locked_until > 0:
                self._attempts.pop(session_id, None)

        totp_required = self.method in ("totp", "both")
        password_required = self.method in ("password", "both")

        # Load and check seeds/hashes from vault
        if totp_required:
            seed = await self.vault.get(self.plugin_id, "TOTP_SEED")
            if not seed:
                return {
                    "status": "error",
                    "error": "not_provisioned",
                    "message": "TOTP is not provisioned.",
                }

        if password_required:
            password_hash = await self.vault.get(self.plugin_id, "PASSWORD_HASH")
            if not password_hash:
                return {
                    "status": "error",
                    "error": "not_provisioned",
                    "message": "Password is not provisioned.",
                }

        # Verify TOTP if required
        if totp_required:
            if not totp:
                count, locked_until = self._record_failure(session_id)
                if locked_until is not None:
                    return {
                        "status": "error",
                        "error": "locked",
                        "message": "Too many failed elevation attempts. Session locked.",
                        "locked_until": locked_until,
                    }
                return {
                    "status": "error",
                    "error": "invalid_factor",
                    "message": "TOTP code is required.",
                    "attempts_remaining": self.max_attempts - count,
                }

            # TOTP replay protection
            time_step = int(now) // 30
            subject_replay = self._replay.setdefault(subject, set())
            if (totp, time_step) in subject_replay:
                count, locked_until = self._record_failure(session_id)
                if locked_until is not None:
                    return {
                        "status": "error",
                        "error": "locked",
                        "message": "Too many failed elevation attempts. Session locked.",
                        "locked_until": locked_until,
                    }
                return {
                    "status": "error",
                    "error": "replay",
                    "message": "TOTP code has already been used in this window.",
                    "attempts_remaining": self.max_attempts - count,
                }

            # Verify TOTP code validity
            try:
                totp_verifier = pyotp.TOTP(seed)
                is_valid = totp_verifier.verify(totp, valid_window=1)
            except Exception as e:
                logger.error("TOTP verification error: %s", e)
                is_valid = False

            if not is_valid:
                count, locked_until = self._record_failure(session_id)
                if locked_until is not None:
                    return {
                        "status": "error",
                        "error": "locked",
                        "message": "Too many failed elevation attempts. Session locked.",
                        "locked_until": locked_until,
                    }
                return {
                    "status": "error",
                    "error": "invalid_factor",
                    "message": "Invalid TOTP code.",
                    "attempts_remaining": self.max_attempts - count,
                }

            # Prevent replay of successful TOTP codes
            subject_replay.add((totp, time_step))
            to_remove = {item for item in subject_replay if item[1] < time_step - 2}
            subject_replay.difference_update(to_remove)

        # Verify Password if required
        if password_required:
            if not password:
                count, locked_until = self._record_failure(session_id)
                if locked_until is not None:
                    return {
                        "status": "error",
                        "error": "locked",
                        "message": "Too many failed elevation attempts. Session locked.",
                        "locked_until": locked_until,
                    }
                return {
                    "status": "error",
                    "error": "invalid_factor",
                    "message": "Password is required.",
                    "attempts_remaining": self.max_attempts - count,
                }

            try:
                ph = PasswordHasher()
                is_valid = ph.verify(password_hash, password)
            except VerifyMismatchError:
                is_valid = False
            except Exception as e:
                logger.error("Password verification error: %s", e)
                is_valid = False

            if not is_valid:
                count, locked_until = self._record_failure(session_id)
                if locked_until is not None:
                    return {
                        "status": "error",
                        "error": "locked",
                        "message": "Too many failed elevation attempts. Session locked.",
                        "locked_until": locked_until,
                    }
                return {
                    "status": "error",
                    "error": "invalid_factor",
                    "message": "Invalid password.",
                    "attempts_remaining": self.max_attempts - count,
                }

        # Clear failure tracking and mint token on success
        self._attempts.pop(session_id, None)

        prior_token = self._by_session.pop(session_id, None)
        if prior_token:
            self._tokens.pop(prior_token, None)

        token = secrets.token_urlsafe(32)
        expires_at = now + self.ttl_seconds

        self._tokens[token] = (session_id, subject, expires_at)
        self._by_session[session_id] = token

        logger.info(
            "AUDIT terminal_elevation_granted session=%s subject=%s expires_at=%s ttl=%s",
            session_id,
            subject,
            expires_at,
            self.ttl_seconds,
        )

        return {
            "status": "ok",
            "token": token,
            "expires_at": expires_at,
            "ttl": self.ttl_seconds,
        }

    def is_elevated(self, session_id: str) -> bool:
        """Check if a session has a valid, non-expired elevation token."""
        self._gc()
        token = self._by_session.get(session_id)
        if not token:
            return False
        entry = self._tokens.get(token)
        if not entry:
            return False
        _, _, expires_at = entry
        return expires_at > time.time()

    def peek(self, session_id: str) -> float | None:
        """Get the expiration timestamp of the session's token if valid and active, else None."""
        self._gc()
        token = self._by_session.get(session_id)
        if not token:
            return None
        entry = self._tokens.get(token)
        if not entry:
            return None
        _, _, expires_at = entry
        return expires_at if expires_at > time.time() else None

    def clear(self, session_id: str) -> None:
        """Clear all session state: token, attempts, and session mapping."""
        token = self._by_session.pop(session_id, None)
        if token:
            self._tokens.pop(token, None)
        self._attempts.pop(session_id, None)
