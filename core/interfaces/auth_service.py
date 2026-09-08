"""Protocol interface for plugin-facing inbound-auth resolution.

Covers exactly what ``cat_terminal_relay_plugin`` needed to hand-roll before
this existed: bearer -> principal (API key or Layer-1 JWT), session cookie ->
subject, and minting a first-party scoped token. Plugins must never reach
``oauth_provider._svc`` / ``OAuthService._mint_jwt`` / ``db_layer.api_key_store``
directly — see ``core/auth_service.py`` for the concrete adapter and
``test_plugin_core_import_boundary.py`` for the enforcement test.

``SessionGateMiddleware`` (``api/middleware.py``) cannot gate a WebSocket
handshake before ``accept()`` — that pre-accept case is exactly why this is a
callable service rather than "just use the middleware": the terminal relay's
WS routes (``AuthPolicy.NONE``) call ``principal_from_bearer`` themselves
inside the handshake, before the socket is accepted.
"""

from datetime import datetime
from typing import Protocol

from core.interfaces.principal import Principal


class IAuthService(Protocol):
    """Protocol defining the plugin-facing inbound-auth interface."""

    async def principal_from_bearer(self, token: str) -> Principal | None:
        """Resolve a bearer token (API key or Layer-1 JWT) to a Principal, or None."""
        ...

    async def principal_from_session_cookie(self, token: str) -> str | None:
        """Resolve an admin session-cookie value to a subject, or None."""
        ...

    async def mint_scoped_token(
        self,
        *,
        subject: str,
        client_id: str,
        scopes: list[str],
        ttl: int,
        token_type: str = "access",
    ) -> tuple[str, str, datetime]:
        """Mint a first-party scoped access token; returns ``(jwt, jti, expires_at)``."""
        ...
