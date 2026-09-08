"""
Pre-accept WebSocket handshake authentication.

Both relay legs authenticate the bearer BEFORE ``websocket.accept()`` and fail
closed: the token must validate (Layer-1 RS256 JWT) AND carry the required
scope. The token is read from the ``token`` query parameter (browsers cannot set
custom WS headers) with an ``Authorization: Bearer`` fallback.

Dev/test seam: when ``CAT_TERMINAL_DEV_AUTH=1`` a token of the form
``dev:<subject>:<scope1,scope2>`` is accepted without RS256 verification. This
lets the mock-extension integration test exercise the byte pump + binding logic
without minting real keypairs. It is OFF by default and loudly logged.
"""

import hashlib
import logging
import os
import time

from starlette.websockets import WebSocket

logger = logging.getLogger("whiskers.plugins")

# Throttle repeat handshake-rejection warnings to prevent log spam from stale tokens reconnecting.
_WARN_THROTTLE_S = 60.0
_last_warned: dict[str, float] = {}


def _throttled_warn(reason: str, token: str, *fmt_args) -> None:
    """Log one WARNING per (reason, token) every ``_WARN_THROTTLE_S``; DEBUG otherwise."""
    now = time.monotonic()

    # Prune stale entries to prevent unbounded memory growth.
    stale_keys = [k for k, v in _last_warned.items() if now - v >= _WARN_THROTTLE_S * 2]
    for k in stale_keys:
        del _last_warned[k]

    key = f"{reason}:{hashlib.sha256(token.encode()).hexdigest()[:12]}"
    last = _last_warned.get(key, 0.0)

    if now - last >= _WARN_THROTTLE_S:
        # Hard cap to prevent unbounded growth from malicious traffic
        if len(_last_warned) >= 10000:
            _last_warned.clear()
        _last_warned[key] = now
        logger.warning(reason, *fmt_args)
    else:
        logger.debug(reason, *fmt_args)


def _extract_token(websocket: WebSocket) -> str | None:
    """Pull the bearer from ?token= or the Authorization header."""
    token = websocket.query_params.get("token")
    if token:
        return token
    auth = websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _has_required_scope(
    scopes: set[str], required_scope: str, *, role: str | None = None, kind=None
) -> bool:
    """Check if caller scopes satisfy required_scope.

    Routed through ``evaluate_access`` instead of a hand-rolled sentinel
    check — that used to duplicate ``is_allowed`` decision logic inline
    (forbidden by the scope-management guardrail: see
    ``test_scope_import_boundary.py``) and, because of the duplication,
    missed grammar implication (a ``core:terminal:write`` grant did not
    satisfy a ``core:terminal:read`` requirement) and role-based admin.
    ``all``/``*``/``admin`` sentinel bypass still applies — it now comes
    from ``core.scope_management.rules``, the single source of truth.
    """
    from core.scope_management import ScopeGrant, evaluate_access
    from core.scope_management.legacy_map import expand_legacy_alias_for_read
    from core.scope_management.principal import PrincipalKind

    required = expand_legacy_alias_for_read(required_scope)
    grant = ScopeGrant(scopes=list(scopes), role=role, kind=kind or PrincipalKind.API_KEY)
    return evaluate_access(grant, required=set(required), path="terminal_relay_handshake").allowed


def _dev_auth(token: str, required_scope: str) -> str | None:
    """Dev-only token parser: ``dev:<subject>:<csv-scopes>``. Returns subject."""
    parts = token.split(":", 2)
    if len(parts) != 3 or parts[0] != "dev":
        return None
    subject = parts[1]
    scopes = {s.strip() for s in parts[2].split(",") if s.strip()}
    if not _has_required_scope(scopes, required_scope):
        return None
    return subject or None


async def authenticate_handshake(
    websocket: WebSocket, required_scope: str
) -> str | None:
    """Validate the WS bearer + scope. Returns the subject, or None to reject."""
    token = _extract_token(websocket)
    if not token:
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
        # Fall through to real validation if not a dev token.

    from core.auth_service import get_auth_service
    from core.scope_management.principal import PrincipalKind

    try:
        principal = await get_auth_service().principal_from_bearer(token)
    except Exception as exc:
        logger.warning("terminal relay handshake: token validation failed: %s", exc)
        return None
    if principal is None:
        _throttled_warn("terminal relay handshake: invalid/unknown/revoked token", token)
        return None

    kind = PrincipalKind.API_KEY if token.startswith("octk_") else PrincipalKind.SESSION_USER
    if not _has_required_scope(
        set(principal.scopes), required_scope, role=principal.role, kind=kind
    ):
        _throttled_warn(
            "terminal relay handshake: missing scope %s (have %s)",
            token, required_scope, principal.scopes,
        )
        return None
    return principal.subject
