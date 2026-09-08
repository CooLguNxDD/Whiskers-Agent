"""
Cookie-authed REST control plane for the Operator Console — /api/terminal/*.

The browser console is HttpOnly-cookie authenticated (no JWT in JS) and calls
MCP tools indirectly through these REST endpoints. ``SessionGateMiddleware``
already gates ``/api/`` behind a valid admin session cookie; each handler also
re-reads the cookie to derive the subject. Subject is used both to bind the relay
session and to mint the short-lived console WS ticket so the three legs share one
identity (see the subject-binding invariant in console_ws / data_ws).

These are late-bound Starlette routes (registered via ``register_http_route``)
because the plugin loads after ``mcp.http_app()`` builds the app.
"""

import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..services import ide_registry, session_registry, elevation_service
from ..services.session_ops import kill_relay_session, open_relay_session
from ..services.ws_ticket import mint_console_ticket
from db_layer.vault import VaultService
import pyotp
from argon2 import PasswordHasher

from core.context import MCP_SERVER_URL as _CORE_MCP_URL

logger = logging.getLogger("whiskers.plugins")


def _to_ws_base(base: str, *, https: bool = False) -> str:
    """Convert http(s) base to ws(s) base. Optionally force wss."""
    ws = base.replace("https://", "wss://").replace("http://", "ws://")
    if https and ws.startswith("ws://"):
        ws = "wss://" + ws[5:]
    return ws


def _console_ws_url(session_id: str, request: Request | None = None) -> str:
    """Build the browser console WS URL.

    Authority (host:port) is taken from MCP_SERVER_URL (source of truth for
    browser-reachable MCP address). Request headers are used *only* for scheme
    detection (https ingress or X-Forwarded-Proto) to produce wss:// when needed.
    This avoids Docker-internal names (e.g. whiskers-agent) that arrive via
    Vite/nginx proxies for the control REST calls.
    """
    base = os.environ.get("MCP_SERVER_URL") or _CORE_MCP_URL
    force_https = False
    if request is not None:
        if request.url.scheme == "https":
            force_https = True
        xfp = (request.headers.get("x-forwarded-proto") or "").lower()
        if "https" in xfp:
            force_https = True
    ws_base = _to_ws_base(base, https=force_https)
    return f"{ws_base.rstrip('/')}/api/terminal/none/ws/{session_id}"


async def _session_subject(request: Request) -> str | None:
    """Derive the subject from the admin session cookie, or None."""
    if os.environ.get("CAT_TERMINAL_DEV_AUTH") == "1":
        dev = os.environ.get("CAT_TERMINAL_DEV_SUBJECT")
        if dev:
            return dev
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        from core.auth_service import get_auth_service
        return await get_auth_service().principal_from_session_cookie(token)
    except Exception as exc:
        logger.warning("terminal control: session cookie validation failed: %s", exc)
        return None


async def list_hosts(request: Request) -> JSONResponse:
    """GET /api/terminal/hosts — online IDE hosts for the session subject."""
    subject = await _session_subject(request)
    if subject is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    return JSONResponse({"status": "ok", "hosts": ide_registry.list_online(subject=subject)})


def _hosts_ws_url(request: Request | None = None) -> str:
    """Build the terminal hosts list WS URL.

    Authority (host:port) is taken from MCP_SERVER_URL (source of truth for
    browser-reachable MCP address). Request headers are used *only* for scheme
    detection (https ingress or X-Forwarded-Proto) to produce wss:// when needed.
    This avoids Docker-internal names (e.g. whiskers-agent) that arrive via
    Vite/nginx proxies for the control REST calls.
    """
    base = os.environ.get("MCP_SERVER_URL") or _CORE_MCP_URL
    force_https = False
    if request is not None:
        if request.url.scheme == "https":
            force_https = True
        xfp = (request.headers.get("x-forwarded-proto") or "").lower()
        if "https" in xfp:
            force_https = True
    ws_base = _to_ws_base(base, https=force_https)
    return f"{ws_base.rstrip('/')}/api/terminal/none/hosts/ws"


async def get_hosts_ws_ticket(request: Request) -> JSONResponse:
    """GET /api/terminal/hosts/ws-ticket — mint a short-lived ticket and return ws url."""
    subject = await _session_subject(request)
    if subject is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    try:
        ticket = await mint_console_ticket(subject)
    except Exception as exc:
        logger.warning("terminal control: hosts ticket mint failed: %s", exc)
        return JSONResponse(
            {"status": "error", "error": "ticket_unavailable", "message": "Failed to generate websocket ticket"},
            status_code=503,
        )
    ws_url = _hosts_ws_url(request)
    return JSONResponse({"status": "ok", "ws_url": ws_url, "ws_ticket": ticket})


async def open_session(request: Request) -> JSONResponse:
    """POST /api/terminal/sessions {ide_id, workdir?} — open + return ws ticket."""
    subject = await _session_subject(request)
    if subject is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ide_id = (body.get("ide_id") or "").strip()
    workdir = body.get("workdir") or None
    if not ide_id:
        return JSONResponse(
            {"status": "error", "error": "missing_required_fields",
             "missing_fields": ["ide_id"], "message": "Please provide: ide_id"},
            status_code=400,
        )

    result = await open_relay_session(subject, ide_id, workdir)
    if result.get("status") != "ok":
        code = 404 if result.get("error") == "ide_offline" else 403
        return JSONResponse(result, status_code=code)

    session = result["session"]
    try:
        ticket = await mint_console_ticket(subject)
    except Exception as exc:
        await kill_relay_session(subject, session.session_id)
        logger.warning("terminal control: ticket mint failed: %s", exc)
        return JSONResponse(
            {"status": "error", "error": "ticket_unavailable", "message": "Failed to generate websocket ticket"},
            status_code=503,
        )
    return JSONResponse({
        "status": "ok",
        "session_id": session.session_id,
        "ws_url": _console_ws_url(session.session_id, request),
        "ws_ticket": ticket,
    })


async def kill_session(request: Request) -> JSONResponse:
    """DELETE /api/terminal/sessions/{session_id} — force-close a session."""
    subject = await _session_subject(request)
    if subject is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    session_id = request.path_params["session_id"]
    result = await kill_relay_session(subject, session_id)
    if result.get("status") != "ok":
        code = 404 if result.get("error") == "not_found" else 403
        return JSONResponse(result, status_code=code)
    return JSONResponse(result)


async def elevate_session(request: Request) -> JSONResponse:
    """POST /api/terminal/sessions/{session_id}/elevate {totp?, password?} — step-up verify + grant."""
    subject = await _session_subject(request)
    if subject is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    session_id = request.path_params["session_id"]
    session = session_registry.get(session_id)
    if session is None:
        return JSONResponse({"status": "error", "error": "not_found"}, status_code=404)
    if session.subject != subject:
        return JSONResponse({"status": "error", "error": "forbidden"}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        body = {}
    totp = body.get("totp")
    password = body.get("password")

    logger.info("AUDIT terminal_elevation_requested session=%s subject=%s", session_id, subject)
    result = await elevation_service.verify_and_mint(session_id, subject, totp=totp, password=password)

    if result.get("status") == "ok":
        await ide_registry.send_control(session.ide_id, {
            "type": "elevation_granted",
            "session_id": session_id,
            "expires_at": result["expires_at"]
        })
        logger.info("AUDIT terminal_elevation_granted session=%s subject=%s", session_id, subject)
        try:
            from core.telemetry import collector
            collector.record_elevation("elevate_ok", subject)
        except Exception:
            logger.debug("control_routes.py: swallowed exception", exc_info=True)
        return JSONResponse({"status": "ok", "expires_at": result["expires_at"], "ttl": result["ttl"]})
    elif result.get("error") == "locked":
        await kill_relay_session(subject, session_id)
        logger.warning("AUDIT terminal_elevation_locked session=%s subject=%s", session_id, subject)
        try:
            from core.telemetry import collector
            collector.record_elevation("elevate_locked", subject)
        except Exception:
            logger.debug("control_routes.py: swallowed exception", exc_info=True)
        return JSONResponse(result, status_code=403)
    else:  # invalid_factor / replay / not_provisioned
        logger.warning("AUDIT terminal_elevation_failed session=%s subject=%s error=%s",
                       session_id, subject, result.get("error"))
        try:
            from core.telemetry import collector
            collector.record_elevation("elevate_fail", subject)
        except Exception:
            logger.debug("control_routes.py: swallowed exception", exc_info=True)
        code = 409 if result.get("error") == "not_provisioned" else 401
        return JSONResponse(result, status_code=code)



async def provision_totp(request: Request) -> JSONResponse:
    """POST /api/terminal/totp/provision — generate pending TOTP seed; self-disables once a final seed exists."""
    subject = await _session_subject(request)
    if subject is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    vault = VaultService()
    if await vault.exists("cat_terminal_relay_plugin", "TOTP_SEED"):
        return JSONResponse(
            {"status": "error", "error": "already_provisioned", "message": "TOTP already provisioned."},
            status_code=409
        )

    seed = pyotp.random_base32()
    await vault.set("cat_terminal_relay_plugin", "PENDING_TOTP_SEED", seed)
    uri = pyotp.TOTP(seed).provisioning_uri(name=subject, issuer_name="Whiskers Agent")
    logger.info("AUDIT terminal_totp_provision_initiated subject=%s", subject)
    return JSONResponse({"status": "ok", "otpauth_uri": uri})


async def totp_status(request: Request) -> JSONResponse:
    """GET /api/terminal/totp/status — check if TOTP is provisioned."""
    subject = await _session_subject(request)
    if subject is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    vault = VaultService()
    totp_provisioned = await vault.exists("cat_terminal_relay_plugin", "TOTP_SEED")
    password_provisioned = await vault.exists("cat_terminal_relay_plugin", "PASSWORD_HASH")
    return JSONResponse({
        "status": "ok",
        "totp_provisioned": totp_provisioned,
        "password_provisioned": password_provisioned
    })


async def verify_totp(request: Request) -> JSONResponse:
    """POST /api/terminal/totp/verify {code} — verify pending TOTP seed and promote to final."""
    subject = await _session_subject(request)
    if subject is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    vault = VaultService()
    if await vault.exists("cat_terminal_relay_plugin", "TOTP_SEED"):
        return JSONResponse(
            {"status": "error", "error": "already_provisioned", "message": "TOTP already provisioned."},
            status_code=409
        )

    pending_seed = await vault.get("cat_terminal_relay_plugin", "PENDING_TOTP_SEED")
    if not pending_seed:
        return JSONResponse(
            {"status": "error", "error": "not_initiated", "message": "TOTP setup not initiated."},
            status_code=400
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    code = (body.get("code") or "").strip()
    if not code:
        return JSONResponse(
            {"status": "error", "error": "missing_required_fields", "missing_fields": ["code"]},
            status_code=400
        )

    totp_obj = pyotp.TOTP(pending_seed)
    if totp_obj.verify(code):
        await vault.set("cat_terminal_relay_plugin", "TOTP_SEED", pending_seed)
        await vault.delete("cat_terminal_relay_plugin", "PENDING_TOTP_SEED")
        logger.info("AUDIT terminal_totp_provision_verified subject=%s", subject)
        return JSONResponse({"status": "ok"})
    else:
        logger.warning("AUDIT terminal_totp_verification_failed subject=%s", subject)
        return JSONResponse(
            {"status": "error", "error": "invalid_code", "message": "Invalid verification code."},
            status_code=400
        )


async def provision_password(request: Request) -> JSONResponse:
    """POST /api/terminal/password/provision {password} — store/replace the argon2 step-up password hash."""
    subject = await _session_subject(request)
    if subject is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        body = {}
    password = (body.get("password") or "").strip()
    if not password:
        return JSONResponse(
            {"status": "error", "error": "missing_required_fields", "missing_fields": ["password"]},
            status_code=400
        )

    hashed = PasswordHasher().hash(password)
    await VaultService().set("cat_terminal_relay_plugin", "PASSWORD_HASH", hashed)
    logger.info("AUDIT terminal_password_provisioned subject=%s", subject)
    return JSONResponse({"status": "ok"})


async def host_token(request: Request) -> JSONResponse:
    """Mint a core:terminal:read token for the session subject."""
    try:
        subject = await _session_subject(request)
        if subject is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)

        from core.auth_service import AuthServiceUnavailable, get_auth_service

        from plugins.cat_terminal_relay_plugin.plugin_config import SETTINGS
        ttl = SETTINGS.get("terminal_host_token_ttl_seconds", 900)
        try:
            token, _, expires_at = await get_auth_service().mint_scoped_token(
                subject=subject,
                client_id="whiskers-host",
                scopes=["core:terminal:read"],
                ttl=ttl,
            )
        except AuthServiceUnavailable:
            return JSONResponse({"status": "error", "error": "oauth_disabled"}, status_code=503)
        logger.info("AUDIT terminal_host_token_minted subject=%s", subject)
        return JSONResponse({
            "status": "ok",
            "token": token,
            "expires_in": ttl,
            "expires_at": expires_at.isoformat(),
        })
    except Exception as exc:
        logger.exception("terminal control: mint_failed")
        return JSONResponse({"status": "error", "error": "mint_failed"}, status_code=500)


async def _sandbox_elevate_api_key_subject(request: Request) -> str | None:
    """Allow a Bearer octk_ API key with a sandbox-exec-capable scope to drive
    sandbox elevation minting, in addition to the admin session cookie.
    Defensive: any failure (including a request object with no .headers,
    as used by some test fixtures) resolves to None, not an exception."""
    try:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        token = auth[7:].strip()
        if not token.startswith("octk_"):
            return None
        from core.auth_service import get_auth_service
        from core.scope_management import ScopeGrant, evaluate_access
        from core.scope_management.legacy_map import expand_legacy_alias_for_read
        from core.scope_management.principal import PrincipalKind

        principal = await get_auth_service().principal_from_bearer(token)
        if principal is None:
            return None
        required = expand_legacy_alias_for_read("core:terminal.sandbox:write")
        grant = ScopeGrant(scopes=list(principal.scopes), kind=PrincipalKind.API_KEY)
        decision = evaluate_access(grant, required=set(required), path="terminal_sandbox_elevate")
        if not decision.allowed:
            return None
        return principal.subject
    except Exception as exc:
        logger.warning("sandbox_elevate: api key lookup failed: %s", exc)
        return None


async def sandbox_elevate(request: Request) -> JSONResponse:
    """POST /api/terminal/sandbox/elevate {totp?, password?} — step-up verify +
    grant a sandbox elevation token keyed by the caller subject."""
    subject = await _session_subject(request)
    if subject is None:
        subject = await _sandbox_elevate_api_key_subject(request)
    if subject is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    totp = body.get("totp")
    password = body.get("password")
    logger.info("AUDIT sandbox_elevation_requested subject=%s", subject)
    result = await elevation_service.verify_and_mint(
        f"sandbox:{subject}", subject, totp=totp, password=password)
    if result.get("status") == "ok":
        logger.info("AUDIT sandbox_elevation_granted subject=%s", subject)
        try:
            from core.telemetry import collector
            collector.record_elevation("sandbox_elevate_ok", subject)
        except Exception:
            logger.debug("control_routes.py: swallowed exception", exc_info=True)
        return JSONResponse({"status": "ok",
                             "expires_at": result["expires_at"],
                             "ttl": result["ttl"]})
    elif result.get("error") == "locked":
        logger.warning("AUDIT sandbox_elevation_locked subject=%s", subject)
        return JSONResponse(result, status_code=403)
    else:
        code = 409 if result.get("error") == "not_provisioned" else 401
        logger.warning("AUDIT sandbox_elevation_failed subject=%s error=%s",
                       subject, result.get("error"))
        return JSONResponse(result, status_code=code)


def register_control_routes() -> None:
    """Register the REST control-plane routes (late-bound, idempotent)."""
    from core.context import http_route_registry
    from core.http_route_registry import AuthPolicy

    http_route_registry.register_http_route("/api/terminal/session_gated/hosts", list_hosts, methods=["GET"],
                        name="terminal_list_hosts", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
    http_route_registry.register_http_route("/api/terminal/session_gated/hosts/ws-ticket", get_hosts_ws_ticket, methods=["GET"],
                        name="terminal_hosts_ws_ticket", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
    http_route_registry.register_http_route("/api/terminal/session_gated/sessions", open_session, methods=["POST"],
                        name="terminal_open_session", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
    http_route_registry.register_http_route("/api/terminal/session_gated/sessions/{session_id}", kill_session,
                        methods=["DELETE"], name="terminal_kill_session", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
    http_route_registry.register_http_route("/api/terminal/session_gated/sessions/{session_id}/elevate", elevate_session,
                        methods=["POST"], name="terminal_elevate_session", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
    http_route_registry.register_http_route("/api/terminal/session_gated/sandbox/elevate",
                        sandbox_elevate, methods=["POST"], name="terminal_sandbox_elevate",
                        owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
    http_route_registry.register_http_route("/api/terminal/session_gated/totp/provision", provision_totp,
                        methods=["POST"], name="terminal_provision_totp", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
    http_route_registry.register_http_route("/api/terminal/session_gated/totp/status", totp_status, methods=["GET"],
                        name="terminal_totp_status", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
    http_route_registry.register_http_route("/api/terminal/session_gated/totp/verify", verify_totp, methods=["POST"],
                        name="terminal_verify_totp", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
    http_route_registry.register_http_route("/api/terminal/session_gated/password/provision", provision_password,
                        methods=["POST"], name="terminal_provision_password", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
    http_route_registry.register_http_route("/api/terminal/session_gated/host-token", host_token, methods=["GET"],
                        name="terminal_host_token", owner="cat_terminal_relay_plugin", auth_policy=AuthPolicy.SESSION_GATED)
