"""




Admin session management routes.

Endpoints
---------
GET    /api/admin/public/exists       — Return whether an admin account has been created in the Vault.
POST   /api/admin/public/login        — Verify admin credentials and issue HttpOnly session cookies.
POST   /api/admin/public/logout       — Revoke session and refresh tokens, then clear cookies.
GET    /api/admin/public/me           — Return current session info extracted from the session cookie.
POST   /api/admin/public/refresh      — Rotate session tokens using the refresh cookie.
POST   /api/admin/public/signup       — Create the admin account (first-time only) and issue session cookies.
"""

import asyncio
import logging
import os
import secrets
import time
from typing import Any
from urllib.parse import quote, urlparse, unquote

from sqlalchemy.dialects.postgresql import insert as pg_insert

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from core.context import mcp, oauth_provider, vault
from db_layer.connection import get_async_session
from db_layer.models import PluginModel
from oauth.oauth_service import InvalidGrantError, InvalidTokenError
from utils.server_config import OAUTH_ACCESS_TOKEN_TTL_SECONDS, OAUTH_REFRESH_TOKEN_TTL_SECONDS

logger = logging.getLogger("whiskers")

# ---------------------------------------------------------------------------
# Rate limiter — in-memory, 5 attempts / 60 s per IP
# ---------------------------------------------------------------------------
_login_attempts: dict[str, list[float]] = {}
_admin_signup_lock = asyncio.Lock()
_login_rate_lock = asyncio.Lock()
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_MAX_IPS = 10_000
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_PRUNE_INTERVAL = 60  # seconds
_last_rate_limit_prune = 0.0


def _prune_login_attempts(now: float) -> None:
    """Drop expired attempt buckets so one-off IPs do not live forever."""
    global _last_rate_limit_prune
    if now - _last_rate_limit_prune < _RATE_LIMIT_PRUNE_INTERVAL:
        return

    window_start = now - _RATE_LIMIT_WINDOW
    expired_ips = []
    for ip, attempts in _login_attempts.items():
        recent_attempts = [t for t in attempts if t > window_start]
        if recent_attempts:
            _login_attempts[ip] = recent_attempts
        else:
            expired_ips.append(ip)

    for ip in expired_ips:
        _login_attempts.pop(ip, None)

    if len(_login_attempts) > _RATE_LIMIT_MAX_IPS:
        excess = len(_login_attempts) - _RATE_LIMIT_MAX_IPS
        for ip in list(_login_attempts.keys())[:excess]:
            _login_attempts.pop(ip, None)

    _last_rate_limit_prune = now


def _check_rate_limit(ip: str) -> int | None:
    """Return None if allowed, or seconds to retry-after if blocked."""
    now = time.monotonic()
    _prune_login_attempts(now)
    window_start = now - _RATE_LIMIT_WINDOW
    attempts = [t for t in _login_attempts.get(ip, []) if t > window_start]
    if attempts:
        _login_attempts[ip] = attempts
    else:
        _login_attempts.pop(ip, None)
    if len(attempts) >= _RATE_LIMIT_MAX:
        oldest = attempts[0]
        retry_after = int(_RATE_LIMIT_WINDOW - (now - oldest)) + 1
        return retry_after
    return None


def _record_attempt(ip: str) -> None:
    """Record a login attempt for a specific IP address."""
    _login_attempts.setdefault(ip, []).append(time.monotonic())


async def _check_rate_limit_async(ip: str) -> int | None:
    """Async-safe rate check under the login lock."""
    async with _login_rate_lock:
        return _check_rate_limit(ip)


async def _record_attempt_async(ip: str) -> int | None:
    """Re-check limit then record under lock. Returns retry-after if blocked."""
    async with _login_rate_lock:
        retry_after = _check_rate_limit(ip)
        if retry_after is not None:
            return retry_after
        _record_attempt(ip)
        return None


async def _ensure_admin_plugin_row() -> None:
    """Upsert a sentinel 'admin' row in the plugins table for FK constraints."""
    stmt = (
        pg_insert(PluginModel)
        .values(
            id="admin",
            display_name="Admin",
            version="1.0.0",
            capabilities=[],
            required_credentials=[],
            external_oauth_providers=[],
            is_active=True,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    async with get_async_session() as session:
        await session.execute(stmt)
        await session.commit()


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------
_IS_DEV = os.environ.get("ENV", "dev") == "dev"


def _set_session_cookie(response: Response, access_jwt: str) -> None:
    """Set the HttpOnly session cookie on the response."""
    response.set_cookie(
        key="session",
        value=access_jwt,
        httponly=True,
        secure=not _IS_DEV,
        samesite="lax",
        max_age=OAUTH_ACCESS_TOKEN_TTL_SECONDS,
        path="/",
    )


def _set_refresh_cookie(response: Response, refresh_jwt: str) -> None:
    """Set the HttpOnly refresh cookie on the response."""
    response.set_cookie(
        key="refresh",
        value=refresh_jwt,
        httponly=True,
        secure=not _IS_DEV,
        samesite="lax",
        max_age=OAUTH_REFRESH_TOKEN_TTL_SECONDS,
        path="/api/admin/public",
    )


def _clear_cookies(response: Response) -> None:
    """Delete session and refresh cookies from the response."""
    response.delete_cookie(key="session", path="/", samesite="lax", secure=not _IS_DEV)
    response.delete_cookie(key="refresh", path="/api/admin/public", samesite="lax", secure=not _IS_DEV)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_oauth_service():
    """Return the OAuthService instance from the global oauth_provider."""
    if oauth_provider is None:
        return None
    return getattr(oauth_provider, "_svc", None)


def _safe_internal_redirect(next_url: str) -> str:
    """Return a safe internal redirect target, or '/' for unsafe input."""
    if not next_url or not isinstance(next_url, str):
        return "/"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or next_url.startswith("//"):
        return "/"

    decoded_url = unquote(next_url)
    normalized_url = decoded_url.strip().replace('\\', '/')
    parsed = urlparse(normalized_url)

    if parsed.scheme or parsed.netloc or not normalized_url.startswith("/") or normalized_url.startswith("//"):
        return "/"

    return next_url


async def _validate_session_cookie(request: Request) -> dict | None:
    """Validate the session cookie and return payload."""
    token = request.cookies.get("session")
    if not token:
        return None
    svc = _get_oauth_service()
    if svc is None:
        logger.error("_validate_session_cookie: OAuth service not available")
        return None
    try:
        return await svc.validate_token(token)
    except Exception:
        logger.exception(
            "_validate_session_cookie: Token validation failed. token_present=True, len=%d",
            len(token)
        )
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@http_route_registry.route(route="admin", endpoint="exists", methods=["GET"], name="admin_exists", auth_policy=AuthPolicy.PUBLIC, owner="api.admin")
async def admin_exists(request: Request) -> Response:
    """Return whether an admin account has been created in the Vault."""
    if vault is None:
        return JSONResponse({"error": "vault_unavailable"}, status_code=503)
    try:
        username = await vault.get("admin", "username")
    except Exception:
        logger.exception("admin_exists: vault lookup failed")
        return JSONResponse({"error": "vault_unavailable"}, status_code=503)
    return JSONResponse({"exists": username is not None})


@http_route_registry.route(route="admin", endpoint="signup", methods=["POST"], name="admin_signup", auth_policy=AuthPolicy.PUBLIC, owner="api.admin")
async def admin_signup(request: Request) -> Response:
    """Create the admin account (first-time only) and issue session cookies."""
    from core.user_management.store import hash_password

    if vault is None:
        return JSONResponse({"error": "vault_unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("admin_login: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    username: str = (body.get("username") or "").strip()
    password: str = body.get("password", "")
    state: str = body.get("state", "")

    missing = []
    if not username:
        missing.append("username")
    if not password:
        missing.append("password")
    if missing:
        return JSONResponse({"error": "missing_fields", "missing": missing}, status_code=400)

    async with _admin_signup_lock:
        existing = await vault.get("admin", "username")
        if existing is not None:
            return JSONResponse({"error": "admin_already_exists"}, status_code=409)

        password_hash = await asyncio.to_thread(hash_password, password)
        await _ensure_admin_plugin_row()
        await vault.set("admin", "username", username)
        await vault.set("admin", "password_hash", password_hash)

    svc = _get_oauth_service()
    if svc is None:
        return JSONResponse({"error": "oauth_service_unavailable"}, status_code=503)

    extra_claims = {
        "whiskers_role": "master",
        "ocat_role": "master",
        "whiskers_tenant": 1,
        "ocat_tenant": 1,
    }

    await svc.ensure_internal_client("admin-console", scopes="admin")
    pair = await svc._issue_token_pair(
        client_id="admin-console",
        scopes=["admin"],
        extra_claims=extra_claims,
    )
    redirect = f"/?state={quote(state, safe='')}" if state else "/"
    response = JSONResponse({"redirect": redirect})
    _set_session_cookie(response, pair["access_token"])
    _set_refresh_cookie(response, pair["refresh_token"])
    logger.info("admin_signup: admin account created")
    return response


@http_route_registry.route(route="admin", endpoint="login", methods=["POST"], name="admin_login", auth_policy=AuthPolicy.PUBLIC, owner="api.admin")
async def admin_login(request: Request) -> Response:
    """Verify admin credentials and issue HttpOnly session cookies."""
    import bcrypt  # imported lazily — optional dep not needed on stdio mode
    from core.user_management import get_user_by_username, verify_password
    from core.api_key_management.scopes import scopes_for_role

    ip = request.client.host if request.client else "unknown"
    retry_after = await _check_rate_limit_async(ip)
    if retry_after is not None:
        return JSONResponse(
            {"error": "too_many_attempts", "retry_after": retry_after},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("admin_login: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    username: str = (body.get("username") or "").strip()
    password: str = body.get("password", "")
    state: str = body.get("state", "")
    next_url: str = body.get("next", "")

    if not username or not password:
        return JSONResponse({"error": "username_and_password_required"}, status_code=400)

    if vault is None:
        return JSONResponse({"error": "vault_unavailable"}, status_code=503)

    user = await get_user_by_username(username)
    if user is not None:
        is_active = user.get("is_active", True)
        stored_hash = user["password_hash"]

        # Record attempt BEFORE checking password to prevent timing-based bypass
        retry_after = await _record_attempt_async(ip)
        if retry_after is not None:
            return JSONResponse(
                {"error": "too_many_attempts", "retry_after": retry_after},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        password_match = await asyncio.to_thread(verify_password, stored_hash, password)
        if not is_active or not password_match:
            return JSONResponse({"error": "invalid_credentials"}, status_code=401)

        svc = _get_oauth_service()
        if svc is None:
            return JSONResponse({"error": "oauth_service_unavailable"}, status_code=503)

        from core.api_key_management.scopes import role_has_admin_bypass
        if role_has_admin_bypass(user["role"]):
            scopes = ["admin"]
        else:
            scopes = scopes_for_role(user["role"])

        extra_claims = {
            "whiskers_role": user["role"],
            "ocat_role": user["role"],
            "whiskers_user_id": str(user["id"]),
            "ocat_user_id": str(user["id"]),
            "whiskers_tenant": int(user.get("tenant_id") or 1),
            "ocat_tenant": int(user.get("tenant_id") or 1),
        }

        await svc.ensure_internal_client("admin-console", scopes="admin")
        pair = await svc._issue_token_pair(
            client_id="admin-console",
            scopes=scopes,
            extra_claims=extra_claims
        )
    else:
        # Fallback: if no users row exists (fresh install pre-seed)
        stored_username = await vault.get("admin", "username")
        stored_hash = await vault.get("admin", "password_hash")
        if stored_username is None or stored_hash is None:
            return JSONResponse({"error": "admin_not_configured"}, status_code=503)

        # Record attempt BEFORE checking password to prevent timing-based bypass
        retry_after = await _record_attempt_async(ip)
        if retry_after is not None:
            return JSONResponse(
                {"error": "too_many_attempts", "retry_after": retry_after},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        password_match = await asyncio.to_thread(verify_password, stored_hash, password)
        username_match = secrets.compare_digest(stored_username.encode(), username.encode())

        if not username_match or not password_match:
            return JSONResponse({"error": "invalid_credentials"}, status_code=401)

        svc = _get_oauth_service()
        if svc is None:
            return JSONResponse({"error": "oauth_service_unavailable"}, status_code=503)

        extra_claims = {
            "whiskers_role": "master",
            "ocat_role": "master",
            "whiskers_tenant": 1,
            "ocat_tenant": 1,
        }

        await svc.ensure_internal_client("admin-console", scopes="admin")
        pair = await svc._issue_token_pair(
            client_id="admin-console",
            scopes=["admin"],
            extra_claims=extra_claims
        )

    redirect = f"/?state={quote(state, safe='')}" if state else _safe_internal_redirect(next_url)
    response = JSONResponse({"redirect": redirect})
    _set_session_cookie(response, pair["access_token"])
    _set_refresh_cookie(response, pair["refresh_token"])
    logger.info("admin_login: session issued for IP=%s", ip)
    return response


@http_route_registry.route(route="admin", endpoint="logout", methods=["POST"], name="admin_logout", auth_policy=AuthPolicy.PUBLIC, owner="api.admin")
async def admin_logout(request: Request) -> Response:
    """Revoke session and refresh tokens, then clear cookies."""
    svc = _get_oauth_service()
    response = JSONResponse({"ok": True})
    _clear_cookies(response)

    if svc is None:
        return response

    for cookie_name in ("session", "refresh"):
        token = request.cookies.get(cookie_name)
        if not token:
            continue
        jti = None
        try:
            payload = await svc.validate_token(token)
            jti = payload.get("jti")
            if jti:
                await svc.revoke_token(jti)
        except Exception as exc:
            logger.warning("admin_logout: token revocation failed cookie=%s jti=%s: %s", cookie_name, jti, exc)

    logger.info("admin_logout: session cleared")
    return response


@http_route_registry.route(route="admin", endpoint="refresh", methods=["POST"], name="admin_refresh", auth_policy=AuthPolicy.PUBLIC, owner="api.admin")
async def admin_refresh(request: Request) -> Response:
    """Rotate session tokens using the refresh cookie."""
    svc = _get_oauth_service()
    if svc is None:
        return JSONResponse({"error": "oauth_service_unavailable"}, status_code=503)

    refresh_token = request.cookies.get("refresh")
    if not refresh_token:
        return JSONResponse({"error": "no_refresh_cookie"}, status_code=401)

    try:
        pair = await svc.refresh_grant(refresh_token)
    except (InvalidGrantError, InvalidTokenError):
        response = JSONResponse({"error": "invalid_refresh_token"}, status_code=401)
        _clear_cookies(response)
        return response
    except Exception:
        logger.exception("admin_refresh: unexpected refresh failure")
        raise

    response = JSONResponse({"ok": True})
    _set_session_cookie(response, pair["access_token"])
    _set_refresh_cookie(response, pair["refresh_token"])
    return response


@http_route_registry.route(route="admin", endpoint="me", methods=["GET"], name="admin_me", auth_policy=AuthPolicy.PUBLIC, owner="api.admin")
async def admin_me(request: Request) -> Response:
    """Return current session info extracted from the session cookie."""
    payload = await _validate_session_cookie(request)
    if payload is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    return JSONResponse({
        "subject": payload.get("sub"),
        "scopes": payload.get("scopes", []),
        "expires_at": payload.get("exp"),
        "iat": payload.get("iat"),
    })
