"""Pre-accept WebSocket handshake authentication for analytics."""

import logging
import os
from starlette.websockets import WebSocket

logger = logging.getLogger("whiskers.telemetry")


def _extract_token(websocket: WebSocket) -> str | None:
    """Pull the bearer from ?token= or the Authorization header."""
    token = websocket.query_params.get("token")
    if token:
        return token
    auth = websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _dev_auth(token: str, required_scope: str) -> str | None:
    """Dev-only token parser: ``dev:<subject>:<csv-scopes>``. Returns subject."""
    parts = token.split(":", 2)
    if len(parts) != 3 or parts[0] != "dev":
        return None
    subject = parts[1]
    scopes = {s.strip() for s in parts[2].split(",") if s.strip()}
    if required_scope not in scopes:
        return None
    return subject or None


async def authenticate_handshake(
    websocket: WebSocket, required_scope: str = "analytics:read"
) -> str | None:
    """Validate the WS bearer + scope. Returns the subject, or None to reject."""
    token = _extract_token(websocket)
    if not token:
        logger.info("analytics handshake: no bearer token on WS")
        return None

    if os.environ.get("CAT_TERMINAL_DEV_AUTH") == "1":
        subject = _dev_auth(token, required_scope)
        if subject is not None:
            logger.warning(
                "CAT_TERMINAL_DEV_AUTH active — accepted dev token for subject=%s "
                "scope=%s (DO NOT USE IN PRODUCTION)",
                subject, required_scope,
            )
            return subject

    try:
        from core.context import oauth_provider
        if oauth_provider is None:
            logger.error("analytics handshake: OAuth not enabled — rejecting")
            return None
        payload = await oauth_provider._svc.validate_token(token)
    except Exception as exc:
        logger.warning("analytics handshake: token validation failed: %s", exc)
        return None

    scopes = set(payload.get("scopes") or [])
    if required_scope not in scopes:
        logger.warning(
            "analytics handshake: missing scope %s (have %s)",
            required_scope, scopes,
        )
        return None
    return payload.get("sub")
