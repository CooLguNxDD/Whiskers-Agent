"""
ASGI middleware for the Whiskers Agent server.

Route configuration — single source of truth for public vs gated paths.
All three middleware classes live here so route policy is visible in one place.
"""

import json
import logging
from urllib.parse import quote as _quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as _Req
from starlette.responses import JSONResponse as _JSON
from starlette.responses import RedirectResponse as _Redir

from core.context import oauth_provider
from utils.server_config import MAX_REGISTER_BODY_BYTES

logger = logging.getLogger("whiskers_agent")


class UnhandledExceptionShieldMiddleware:
    """Outermost ASGI safety net: any HTTP request that escapes every inner
    middleware/route as an unhandled exception gets a generic 500 JSON body
    instead of reaching Uvicorn (which can leak a stack trace / internal paths).

    Must be the outermost layer (wrap everything, including CORS) so it also
    catches exceptions from the custom middleware stack itself, not just routes.
    A Starlette ``HTTPException`` is re-raised as-is — those already carry a
    deliberate status/detail and Starlette's own machinery handles them; only
    a *truly* unhandled exception gets the generic body. If the response has
    already started streaming, the exception is re-raised (a second
    ``http.response.start`` would violate the ASGI spec).
    """

    def __init__(self, app):
        """Initialize with the wrapped ASGI application."""
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        from starlette.exceptions import HTTPException

        started = False

        async def _send(message):
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self._app(scope, receive, _send)
        except HTTPException:
            raise
        except Exception as exc:
            if started:
                # Can't send a second http.response.start — but the exception must not
                # vanish into Uvicorn's (possibly muted/unstructured) own error logging.
                logger.error(
                    "Unhandled exception during active streaming response: %s",
                    type(exc).__name__, exc_info=True,
                )
                raise
            from utils.error_response import safe_error_response
            response = safe_error_response(
                exc, log_ctx="Unhandled exception escaped the middleware stack"
            )
            await response(scope, receive, send)


def sanitize_register_scope(body: bytes, valid_scopes) -> bytes:
    """Intersect a DCR request body's ``scope`` with ``valid_scopes``.

    RFC 7591 §3.2.1 lets a server grant a subset of requested scopes. The MCP
    SDK's ``RegistrationHandler`` instead returns 400 for any out-of-set scope,
    breaking clients that request extra (e.g. OIDC ``openid``/``profile``)
    scopes. Drops unknown scopes so registration succeeds. Returns the body
    unchanged when it is not JSON, has no ``scope``, or ``valid_scopes`` is None.
    """
    if not valid_scopes:
        return body
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return body
    if not isinstance(data, dict) or not data.get("scope"):
        return body
    allowed = set(valid_scopes)
    kept = [s for s in str(data["scope"]).split() if s in allowed]
    new_scope = " ".join(kept)
    if new_scope == data["scope"]:
        return body
    if kept:
        data["scope"] = new_scope
    else:
        # Nothing requested is valid — drop the field so the SDK applies defaults.
        data.pop("scope", None)
    return json.dumps(data).encode("utf-8")

# ---------------------------------------------------------------------------
# Route policy — edit here only
# ---------------------------------------------------------------------------

_FALLBACK_PUBLIC_PREFIXES = (
    "/.well-known/",
    "/oauth/plugin/",
    "/oauth/connect/",
    "/oauth/complete-layer1/",
)

_FALLBACK_GATED_PREFIXES = ("/oauth/authorize", "/connect", "/callback", "/plugins")
_FALLBACK_GATED_EXACT = ("/",)


# Cache entries: None = stale/uninitialized
_cache_public: tuple[str, ...] | None = None
_cache_gated_prefixes: tuple[str, ...] | None = None
_cache_gated_exact: tuple[str, ...] | None = None


def reset_route_policy_cache() -> None:
    """Invalidate the middleware prefix caches (call after route registry mutation)."""
    global _cache_public, _cache_gated_prefixes, _cache_gated_exact
    _cache_public = None
    _cache_gated_prefixes = None
    _cache_gated_exact = None


# Invert core→api dependency: registry notifies us via callback, never imports us.
try:
    from core.route_registry.http_route_registry import register_routes_changed_callback

    register_routes_changed_callback(reset_route_policy_cache)
except Exception:
    logger.debug("middleware: could not register routes-changed callback", exc_info=True)


def _public_prefixes() -> tuple[str, ...]:
    global _cache_public
    if _cache_public is None:
        try:
            from core.http_route_registry import get_http_route_registry
            _cache_public = get_http_route_registry().public_prefixes() or _FALLBACK_PUBLIC_PREFIXES
        except Exception:
            logger.warning("SessionGateMiddleware: public_prefixes registry lookup failed; using fallback")
            return _FALLBACK_PUBLIC_PREFIXES
    return _cache_public


def _gated_prefixes() -> tuple[str, ...]:
    global _cache_gated_prefixes
    if _cache_gated_prefixes is None:
        try:
            from core.http_route_registry import get_http_route_registry
            _cache_gated_prefixes = get_http_route_registry().gated_prefixes() or _FALLBACK_GATED_PREFIXES
        except Exception:
            logger.warning("SessionGateMiddleware: gated_prefixes registry lookup failed; using fallback")
            return _FALLBACK_GATED_PREFIXES
    return _cache_gated_prefixes


def _gated_exact() -> tuple[str, ...]:
    global _cache_gated_exact
    if _cache_gated_exact is None:
        try:
            from core.http_route_registry import get_http_route_registry
            _cache_gated_exact = get_http_route_registry().gated_exact() or _FALLBACK_GATED_EXACT
        except Exception:
            logger.warning("SessionGateMiddleware: gated_exact registry lookup failed; using fallback")
            return _FALLBACK_GATED_EXACT
    return _cache_gated_exact


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class PublicPathMiddleware:
    """Skip FastMCP auth enforcement for known public routes.

    Sets ``scope["auth_bypassed"] = True`` so downstream guards allow the
    request through without a bearer token.  Does NOT short-circuit the
    request itself.
    """

    def __init__(self, app):
        """Initialize PublicPathMiddleware with the ASGI application."""
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path: str = scope.get("path", "")
            norm_path = path
            if norm_path.startswith("/mcp"):
                norm_path = norm_path[4:]

            # Bolt optimization: str.startswith accepts a tuple. This executes
            # the prefix matching check in C, avoiding a slow Python generator loop.
            if norm_path.startswith(_public_prefixes()):
                scope["auth_bypassed"] = True
            else:
                from core.route_registry import parse_api_gate
                if parse_api_gate(norm_path) == "public":
                    scope["auth_bypassed"] = True
        await self._app(scope, receive, send)


class SessionGateMiddleware:
    """Gate frontend routes and /oauth/authorize behind a valid admin session cookie.

    Requests to gated paths without a valid session cookie are redirected to
    ``/admin/login?next=<path>``.  All other paths pass through unchanged.
    """

    def __init__(self, app):
        """Initialize SessionGateMiddleware with the ASGI application."""
        self._app = app

    @staticmethod
    def _mask_session_token(token: str) -> str:
        if len(token) <= 12:
            return f"<masked len={len(token)}>"
        return f"{token[:6]}...{token[-4:]} len={len(token)}"

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path: str = scope.get("path", "")
            norm_path = path
            if norm_path.startswith("/mcp"):
                norm_path = norm_path[4:]

            from core.route_registry import parse_api_gate
            gate = parse_api_gate(norm_path)

            if gate is not None:
                if gate in ("session_gated", "scope_required"):
                    is_gated = True
                else:  # gate in ("public", "none")
                    is_gated = False
            else:
                # Bolt optimization: pass tuple directly to str.startswith
                is_gated = norm_path in _gated_exact() or norm_path.startswith(_gated_prefixes())

            if is_gated:
                req = _Req(scope, receive)
                session_token = req.cookies.get("session")
                is_valid = False
                session_payload: dict | None = None
                _provider = oauth_provider
                if _provider is None:
                    from core.context import oauth_provider as _ctx_oauth_provider
                    _provider = _ctx_oauth_provider
                if session_token and _provider is not None:
                    svc = getattr(_provider, "_svc", None)
                    if svc is not None:
                        try:
                            session_payload = await svc.validate_token(session_token)
                            is_valid = True
                        except Exception as exc:
                            logger.exception(
                                "SessionGateMiddleware: svc.validate_token failed "
                                "for oauth_provider=%s service=%s session_token=%s: %s",
                                type(oauth_provider).__name__,
                                type(svc).__name__,
                                self._mask_session_token(session_token),
                                exc,
                            )

                if not is_valid:
                    if norm_path.startswith("/api/"):
                        response = _JSON(
                            {"error": "unauthenticated"}, status_code=401
                        )
                        await response(scope, receive, send)
                        return

                    qs = scope.get("query_string", b"").decode()
                    full_path = norm_path + ("?" + qs if qs else "")

                    from core.context import FRONTEND_URL
                    if FRONTEND_URL:
                        redirect_url = f"{FRONTEND_URL}/login?next={_quote(full_path, safe='')}"
                    else:
                        redirect_url = f"/login?next={_quote(full_path, safe='')}"

                    response = _Redir(url=redirect_url, status_code=302)
                    await response(scope, receive, send)
                    return

                # Scope step, gated on required_scopes being non-empty for
                # this path — NOT on the AuthPolicy.SCOPE_REQUIRED gate
                # segment (moving routes between segments would churn every
                # hardcoded frontend URL builder). Empty required_scopes
                # (the overwhelming majority of routes today) is today's
                # exact behaviour: session-valid is sufficient, no change.
                method = scope.get("method", "GET")
                scope_denial = self._check_required_scopes(norm_path, session_payload, method=method)
                if scope_denial is not None:
                    await scope_denial(scope, receive, send)
                    return

        await self._app(scope, receive, send)

    @staticmethod
    def _check_required_scopes(norm_path: str, session_payload: dict | None, method: str | None = None):
        """Return a 403 JSONResponse when this path's required_scopes deny the
        session grant, else None. Never raises — a lookup/evaluation failure
        degrades to "no scope requirement" (today's behaviour) rather than
        blocking a valid session on an internal error. Reuses the JWT payload
        already decoded by the session-validity check above — no second
        ``validate_token`` round trip per request.
        """
        try:
            from core.route_registry.http_route_registry import get_http_route_registry

            required = get_http_route_registry().required_scopes_for(norm_path, method=method)
            if not required:
                return None

            from core.scope_management import ScopeGrant, evaluate_access
            from core.scope_management.principal import PrincipalKind

            payload = session_payload or {}
            scopes = payload.get("scopes")
            if scopes is None:
                scopes = payload.get("scope")
            scopes_list = scopes.split() if isinstance(scopes, str) else (scopes if isinstance(scopes, list) else [])
            role = payload.get("whiskers_role") or payload.get("ocat_role")
            grant = ScopeGrant(scopes=scopes_list, role=role, kind=PrincipalKind.SESSION_USER)
            decision = evaluate_access(grant, required=set(required), path=norm_path)
            if not decision.allowed:
                return _JSON(
                    {"error": "scope_denied", "required_scopes": sorted(required)},
                    status_code=403,
                )
        except Exception:
            logger.warning("SessionGateMiddleware: scope check failed for %s", norm_path, exc_info=True)
        return None


class RegisterScopeSanitizerMiddleware:
    """Make ``POST /register`` (Dynamic Client Registration) scope-lenient.

    Rewrites the request body so its ``scope`` is the intersection of what the
    client asked for and the server's ``valid_scopes``, before the SDK's strict
    ``RegistrationHandler`` runs. Keeps the SDK route + metadata advertising
    intact; only stops the scope-mismatch 400. All other requests pass through.
    """

    def __init__(self, app):
        """Initialize RegisterScopeSanitizerMiddleware with the ASGI application."""
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith("/mcp"):
            path = path[4:]
        if path != "/register" or oauth_provider is None:
            await self._app(scope, receive, send)
            return

        # Buffer the full request body (capped to prevent DoS).
        body = b""
        more = True
        while more:
            msg = await receive()
            if msg.get("type") != "http.request":
                # Unexpected (e.g. disconnect) — replay what we have and bail.
                break
            body += msg.get("body", b"")
            if len(body) > MAX_REGISTER_BODY_BYTES:
                response = _JSON(
                    {"error": "payload_too_large"},
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            more = msg.get("more_body", False)

        opts = getattr(oauth_provider, "client_registration_options", None)
        valid_scopes = getattr(opts, "valid_scopes", None) if opts else None
        new_body = sanitize_register_scope(body, valid_scopes)

        if new_body != body:
            # Patch Content-Length so downstream length checks stay consistent.
            headers = [
                (k, v) for (k, v) in scope.get("headers", [])
                if k.lower() != b"content-length"
            ]
            headers.append((b"content-length", str(len(new_body)).encode()))
            scope = {**scope, "headers": headers}
            logger.info("RegisterScopeSanitizer: rewrote /register scope to fit valid_scopes")

        sent = False

        async def _receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": new_body, "more_body": False}
            return {"type": "http.disconnect"}

        await self._app(scope, _receive, send)


class AutoRegisterMiddleware(BaseHTTPMiddleware):
    """Intercept /authorize and auto-register clients that skip /register.

    Handles Claude Desktop, MCP Inspector, and similar clients that send a
    client_id + redirect_uri directly to /authorize without pre-registering.
    """

    async def dispatch(self, request, call_next):
        """Intercept and dispatch requests, auto-registering clients on authorize paths if needed."""
        path = request.url.path
        if path.startswith("/mcp"):
            path = path[4:]
        if path == "/authorize" and oauth_provider is not None:
            client_id = request.query_params.get("client_id")
            redirect_uri = request.query_params.get("redirect_uri")
            if client_id and redirect_uri:
                await oauth_provider.auto_register_client(client_id, redirect_uri)
        return await call_next(request)

