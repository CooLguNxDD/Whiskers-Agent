"""Short-lived console WebSocket ticket minting for analytics."""

import logging
import os

logger = logging.getLogger("whiskers.telemetry")

_TICKET_TTL_SECONDS = 60
_TICKET_CLIENT_ID = "cat-analytics-console"


async def mint_analytics_ticket(subject: str) -> str:
    """Mint a 60s analytics:read ticket for ``subject`` (RS256, or dev token)."""
    from core.context import oauth_provider

    if oauth_provider is None:
        if os.environ.get("CAT_TERMINAL_DEV_AUTH") == "1":
            logger.warning(
                "CAT_TERMINAL_DEV_AUTH active — issuing dev analytics ticket for %s", subject
            )
            return f"dev:{subject}:analytics:read"
        raise RuntimeError("OAuth not enabled — cannot mint analytics WS ticket")

    svc = oauth_provider._svc
    kid = await svc.ensure_keypair()
    await svc.ensure_internal_client(_TICKET_CLIENT_ID, scopes="analytics:read")
    token, _, _ = await svc._mint_jwt(
        kid,
        subject=subject,
        client_id=_TICKET_CLIENT_ID,
        scopes=["analytics:read"],
        ttl=_TICKET_TTL_SECONDS,
        token_type="access",
    )
    return token
