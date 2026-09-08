"""Concrete ``IAuthService`` adapter — the plugin-facing inbound-auth boundary.

Wraps the Layer-1 ``OAuthService`` (``core.context._oauth_svc``) and the
API-key store (``core.api_key_management.store``) behind one surface, so
plugin code never touches ``oauth_provider._svc`` / ``OAuthService._mint_jwt``
/ ``db_layer.api_key_store`` directly. Mounted on ``PluginContext`` as
``ctx.auth_service``, and importable standalone via ``get_auth_service()`` for
the many call sites (route handlers, ``@mcp.tool()`` functions, pre-accept WS
handshakes) that never receive a ``PluginContext`` — see
``core/interfaces/auth_service.py``'s module docstring for why the WS
pre-accept case specifically rules out a middleware-only fix.
"""
from __future__ import annotations

import logging
from datetime import datetime

from core.interfaces.principal import Principal

logger = logging.getLogger("whiskers.auth_service")


class AuthServiceUnavailable(RuntimeError):
    """Raised when OAuth/DB is not configured for this deployment."""


class AuthService:
    """Concrete ``IAuthService`` implementation over ``OAuthService`` + the API-key store."""

    async def principal_from_bearer(self, token: str) -> Principal | None:
        """Resolve a bearer token (``octk_`` API key or Layer-1 JWT) to a Principal.

        Returns None on a missing/invalid/expired token — never raises for a
        bad token (only ``AuthServiceUnavailable`` if OAuth itself isn't
        configured, matching the JWT-path callers' existing guard).
        """
        if not token:
            return None

        if token.startswith("octk_"):
            from core.api_key_management.store import lookup_active_by_token
            from core.scope_management import resolve_api_key_scopes

            row = await lookup_active_by_token(token)
            if not row:
                return None
            subject = row.get("subject") or f"api-key:{row['key_id']}"
            return Principal(subject=subject, scopes=frozenset(resolve_api_key_scopes(row)))

        svc = self._require_svc()
        try:
            payload = await svc.validate_token(token)
        except Exception as exc:
            logger.warning("AuthService.principal_from_bearer: JWT validation failed: %s", exc)
            return None
        subject = payload.get("sub")
        if not subject:
            return None
        return Principal(
            subject=subject,
            scopes=frozenset(payload.get("scopes") or []),
            role=payload.get("ocat_role"),
        )

    async def principal_from_session_cookie(self, token: str) -> str | None:
        """Resolve an admin session-cookie value (a Layer-1 JWT) to a subject."""
        if not token:
            return None
        svc = self._require_svc()
        try:
            payload = await svc.validate_token(token)
        except Exception as exc:
            logger.warning(
                "AuthService.principal_from_session_cookie: validation failed: %s", exc
            )
            return None
        return payload.get("sub")

    async def mint_scoped_token(
        self,
        *,
        subject: str,
        client_id: str,
        scopes: list[str],
        ttl: int,
        token_type: str = "access",
    ) -> tuple[str, str, datetime]:
        """Mint a first-party scoped access token; returns ``(jwt, jti, expires_at)``.

        Absorbs the ``ensure_internal_client`` -> ``ensure_keypair`` ->
        ``_mint_jwt`` sequence callers previously hand-rolled against the
        private ``_svc``/``_mint_jwt`` surface.
        """
        svc = self._require_svc()
        await svc.ensure_internal_client(client_id, scopes=" ".join(scopes))
        kid = await svc.ensure_keypair()
        return await svc._mint_jwt(
            kid,
            subject=subject,
            client_id=client_id,
            scopes=scopes,
            ttl=ttl,
            token_type=token_type,
        )

    @staticmethod
    def _require_svc():
        from core.context import _oauth_svc

        if _oauth_svc is None:
            raise AuthServiceUnavailable(
                "OAuthService is not available — ensure DATABASE_URL and OAuth are configured."
            )
        return _oauth_svc


_instance: AuthService | None = None


def get_auth_service() -> AuthService:
    """Return the process-wide ``AuthService`` singleton."""
    global _instance
    if _instance is None:
        _instance = AuthService()
    return _instance
