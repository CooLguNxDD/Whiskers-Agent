"""
Cat Terminal Relay — MCP tools (Phase 1).

Two-step open flow:
  1. request_terminal_token()            → short-lived Layer-2 token (terminal:use)
  2. open_terminal_session(ide, token)   → bind a relay session, get the console WS URL

Plus list_ide_hosts() and kill_terminal_session(). Every tool returns a
structured dict and never raises (per the error-handling-wrapper skill). Subject
is derived from the validated request bearer so it matches the WS handshake.
"""

import logging
import os

from utils.error_response import tool_error
from core.context import mcp

from ..services import ide_registry, token_service
from ..services.session_ops import kill_relay_session, open_relay_session

logger = logging.getLogger("whiskers.plugins")

_TAGS = {"cat_terminal_relay_plugin"}


async def _caller_identity() -> tuple[str | None, set[str]]:
    """Return (subject, scopes) for the current MCP caller, or (None, set())."""
    if os.environ.get("CAT_TERMINAL_DEV_AUTH") == "1":
        dev = os.environ.get("CAT_TERMINAL_DEV_SUBJECT")
        if dev:
            return dev, {"terminal:use", "terminal:host"}
    try:
        from fastmcp.server.dependencies import get_http_headers
        headers = get_http_headers() or {}
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        if not token:
            return None, set()

        from core.auth_service import get_auth_service
        principal = await get_auth_service().principal_from_bearer(token)
        if principal is None:
            return None, set()
        return principal.subject, set(principal.scopes)
    except Exception as exc:
        logger.warning("terminal relay: caller identity resolution failed: %s", exc)
        return None, set()


def _console_ws_url(session_id: str) -> str:
    """Build the browser console WS URL from MCP_SERVER_URL."""
    base = os.environ.get("MCP_SERVER_URL", "http://localhost:10000")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base.rstrip('/')}/api/terminal/none/ws/{session_id}"


@mcp.tool(title="terminal", tags=_TAGS, annotations={"readOnlyHint": False})
async def request_terminal_token() -> dict:
    """Step 1: request a 5-minute terminal session token (requires core:terminal:write scope)."""
    subject, scopes = await _caller_identity()
    if subject is None:
        return tool_error("unauthorized", "No valid terminal credentials on this request.")
    from core.scope_management import ScopeGrant, evaluate_access
    from core.scope_management.principal import PrincipalKind
    from core.scope_management.request import AccessRequest
    from core.route_registry.operation_descriptor import AccessClass

    grant = ScopeGrant(scopes=list(scopes), kind=PrincipalKind.API_KEY)
    req = AccessRequest(
        core_domain="terminal",
        access=AccessClass.WRITE,
        tool_name="request_terminal_token",
        plugin_id="cat_terminal_relay_plugin",
        tags=tuple(_TAGS),
    )
    decision = evaluate_access(grant, required={"core:terminal:write"}, request=req)
    if not decision.allowed:
        return tool_error("forbidden", "Missing required scope: core:terminal:write.")
    token, ttl = token_service.issue(subject)
    logger.info("AUDIT terminal_token_issued subject=%s", subject)
    return {"status": "ok", "token": token, "expires_in": ttl}


@mcp.tool(title="terminal", tags=_TAGS, annotations={"readOnlyHint": True, "idempotentHint": True})
async def list_ide_hosts() -> dict:
    """List the caller's currently-online IDE extension hosts available to relay to (requires core:terminal:read scope)."""
    subject, scopes = await _caller_identity()
    if subject is None:
        return tool_error("unauthorized", "No valid terminal credentials on this request.")
    from core.scope_management import ScopeGrant, evaluate_access
    from core.scope_management.principal import PrincipalKind
    from core.scope_management.request import AccessRequest
    from core.route_registry.operation_descriptor import AccessClass

    grant = ScopeGrant(scopes=list(scopes), kind=PrincipalKind.API_KEY)
    req = AccessRequest(
        core_domain="terminal",
        access=AccessClass.READ,
        tool_name="list_ide_hosts",
        plugin_id="cat_terminal_relay_plugin",
        tags=tuple(_TAGS),
    )
    decision = evaluate_access(grant, required={"core:terminal:read"}, request=req)
    if not decision.allowed:
        return tool_error("forbidden", "Missing required scope: core:terminal:read.")
    hosts = ide_registry.list_online(subject=subject)
    return {"status": "ok", "hosts": hosts}


@mcp.tool(title="terminal", tags=_TAGS, annotations={"readOnlyHint": False})
async def open_terminal_session(
    ide_id: str, terminal_token: str, workdir: str | None = None
) -> dict:
    """Step 2: open a relay session to an online IDE. Returns session_id + console WS URL."""
    missing = []
    if not ide_id or not ide_id.strip():
        missing.append("ide_id")
    if not terminal_token or not terminal_token.strip():
        missing.append("terminal_token")
    if missing:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": missing,
            "message": "Please provide: " + ", ".join(missing),
        }

    subject, scopes = await _caller_identity()
    if subject is None:
        return tool_error("unauthorized", "No valid terminal credentials on this request.")
    from core.scope_management import ScopeGrant, evaluate_access
    from core.scope_management.principal import PrincipalKind
    from core.scope_management.request import AccessRequest
    from core.route_registry.operation_descriptor import AccessClass

    grant = ScopeGrant(scopes=list(scopes), kind=PrincipalKind.API_KEY)
    req = AccessRequest(
        core_domain="terminal",
        access=AccessClass.WRITE,
        tool_name="open_terminal_session",
        plugin_id="cat_terminal_relay_plugin",
        tags=tuple(_TAGS),
    )
    decision = evaluate_access(grant, required={"core:terminal:write"}, request=req)
    if not decision.allowed:
        return tool_error("forbidden", "Missing required scope: core:terminal:write.")

    token_subject = token_service.consume(terminal_token)
    if token_subject is None:
        return tool_error("invalid_token", "Terminal token is invalid, expired, or already used.")
    if token_subject != subject:
        logger.warning("AUDIT terminal_token_subject_mismatch token=%s caller=%s",
                       token_subject, subject)
        return tool_error("forbidden", "Terminal token does not belong to the caller.")

    result = await open_relay_session(subject, ide_id, workdir)
    if result.get("status") != "ok":
        return result
    session = result["session"]
    return {
        "status": "ok",
        "session_id": session.session_id,
        "ws_url": _console_ws_url(session.session_id),
    }


@mcp.tool(title="terminal", tags=_TAGS, annotations={"readOnlyHint": False})
async def kill_terminal_session(session_id: str) -> dict:
    """Force-close a session and kill its relay legs."""
    if not session_id or not session_id.strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["session_id"],
            "message": "Please provide: session_id",
        }
    subject, scopes = await _caller_identity()
    if subject is None:
        return tool_error("unauthorized", "No valid terminal credentials on this request.")
    from core.scope_management import ScopeGrant, evaluate_access
    from core.scope_management.principal import PrincipalKind
    from core.scope_management.request import AccessRequest
    from core.route_registry.operation_descriptor import AccessClass

    grant = ScopeGrant(scopes=list(scopes), kind=PrincipalKind.API_KEY)
    req = AccessRequest(
        core_domain="terminal",
        access=AccessClass.WRITE,
        tool_name="kill_terminal_session",
        plugin_id="cat_terminal_relay_plugin",
        tags=tuple(_TAGS),
    )
    decision = evaluate_access(grant, required={"core:terminal:write"}, request=req)
    if not decision.allowed:
        return tool_error("forbidden", "Missing required scope: core:terminal:write.")
    return await kill_relay_session(subject, session_id)
