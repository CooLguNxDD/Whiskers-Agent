"""
Short-lived console WebSocket ticket minting.

The Operator Console is HttpOnly-cookie authenticated and holds no JWT in JS, but
the console WS leg (``/terminal/ws/{session_id}``) authenticates a ``core:terminal:write``
RS256 bearer in the ``?token=`` query param. This helper mints a short-TTL
``core:terminal:write`` access token bound to the session subject so the browser can put
it in the WS URL. ``console_ws`` validates it unchanged and re-checks
``session.subject == token.sub``; the 60s TTL only gates the handshake (the
accepted socket persists afterwards).

Dev/test seam: when ``CAT_TERMINAL_DEV_AUTH=1`` and OAuth is not enabled, returns a
``dev:<subject>:core:terminal:write`` token that ``routes/auth.py`` accepts without RS256.
"""

import logging
import os

logger = logging.getLogger("whiskers.plugins")

_TICKET_TTL_SECONDS = 60
_TICKET_CLIENT_ID = "cat-terminal-console"


async def mint_console_ticket(subject: str) -> str:
    """Mint a 60s core:terminal:write ticket for ``subject`` (RS256, or dev token)."""
    from core.auth_service import AuthServiceUnavailable, get_auth_service

    try:
        token, _, _ = await get_auth_service().mint_scoped_token(
            subject=subject,
            client_id=_TICKET_CLIENT_ID,
            scopes=["core:terminal:write"],
            ttl=_TICKET_TTL_SECONDS,
        )
        return token
    except AuthServiceUnavailable:
        if os.environ.get("CAT_TERMINAL_DEV_AUTH") == "1":
            logger.warning(
                "CAT_TERMINAL_DEV_AUTH active — issuing dev console ticket for %s", subject
            )
            return f"dev:{subject}:core:terminal:write"
        raise RuntimeError("OAuth not enabled — cannot mint console WS ticket")
