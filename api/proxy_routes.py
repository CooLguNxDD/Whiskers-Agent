"""



REST control plane API routes for upstream MCP proxies.

Endpoints
---------
GET    /api/proxies/session_gated                      — List all registered upstream proxies.
POST   /api/proxies/session_gated                      — Register and mount a new upstream proxy.
DELETE /api/proxies/session_gated/{name}               — Unmount and delete an upstream proxy.
PATCH  /api/proxies/session_gated/{name}               — Update an upstream proxy's custom description.
POST   /api/proxies/session_gated/{name}/oauth/start   — Initiate OAuth 2.0 PKCE flow for a proxy and return authorization URL.
POST   /api/proxies/session_gated/{name}/reindex       — Re-snapshot, re-contribute, and re-embed routes for an upstream proxy.
POST   /api/proxies/session_gated/{name}/test          — Test and re-connect to an upstream proxy, updating its status.
"""

import asyncio
import logging
import re
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from core.context import mcp, oauth_provider
from core.proxy.proxy_manager import proxy_manager
from db_layer.route_store import delete_plugin_route_embeddings
from utils.server_config import MAX_PROXY_CUSTOM_DESCRIPTION_CHARS

logger = logging.getLogger("whiskers")

# ---------------------------------------------------------------------------
# Rate limiter — 1 reindex per proxy per 5 minutes (300 s)
# ---------------------------------------------------------------------------
_REINDEX_RATE_LIMIT_WINDOW = 300.0  # 5 minutes
_REINDEX_RATE_LIMIT_MAX = 1
_reindex_attempts: dict[str, list[float]] = {}
_reindex_rate_lock = asyncio.Lock()


def _check_reindex_rate_limit(proxy_name: str) -> int | None:
    """Return None if allowed, or seconds to retry-after if blocked."""
    now = time.monotonic()
    window_start = now - _REINDEX_RATE_LIMIT_WINDOW
    attempts = [t for t in _reindex_attempts.get(proxy_name, []) if t > window_start]
    if attempts:
        _reindex_attempts[proxy_name] = attempts
    else:
        _reindex_attempts.pop(proxy_name, None)
    if len(attempts) >= _REINDEX_RATE_LIMIT_MAX:
        oldest = attempts[0]
        retry_after = int(_REINDEX_RATE_LIMIT_WINDOW - (now - oldest)) + 1
        return max(1, retry_after)
    return None


async def _record_reindex_attempt_async(proxy_name: str) -> int | None:
    """Re-check limit then record under lock. Returns retry-after if blocked."""
    async with _reindex_rate_lock:
        retry_after = _check_reindex_rate_limit(proxy_name)
        if retry_after is not None:
            return retry_after
        _reindex_attempts.setdefault(proxy_name, []).append(time.monotonic())
        return None


def _get_oauth_service():
    """Return the OAuthService instance from the global oauth_provider."""
    if oauth_provider is None:
        return None
    return getattr(oauth_provider, "_svc", None)


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
        logger.exception("_validate_session_cookie: token validation failed")
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@http_route_registry.route(
    route="proxies",
    endpoint="",
    methods=["GET"],
    name="list_proxies",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.proxy:read",),
    owner="api.proxy",
)
async def list_proxies_route(request: Request) -> Response:
    """List all registered upstream proxies."""
    payload = await _validate_session_cookie(request)
    if payload is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    try:
        proxies = await proxy_manager.list_proxies()
        return JSONResponse(proxies)
    except Exception as e:
        logger.exception("list_proxies route error")
        return JSONResponse(
            {"error": "internal_error", "message": "An unexpected error occurred."},
            status_code=500,
        )


@http_route_registry.route(
    route="proxies",
    endpoint="",
    methods=["POST"],
    name="add_proxy",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.proxy:write",),
    owner="api.proxy",
)
async def add_proxy_route(request: Request) -> Response:
    """Register and mount a new upstream proxy."""
    payload = await _validate_session_cookie(request)
    if payload is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("add_proxy: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    name = (body.get("name") or "").strip()
    transport = (body.get("transport") or "").strip().lower()
    url = (body.get("url") or "").strip()
    auth_mode = (body.get("authMode") or "none").strip().lower()
    bearer_token = body.get("bearerToken")
    oauth_config = body.get("oauthConfig")
    client_secret = body.get("clientSecret")
    custom_description = body.get("customDescription")
    workspace_label = body.get("workspaceLabel")

    if custom_description and len(custom_description) > MAX_PROXY_CUSTOM_DESCRIPTION_CHARS:
        return JSONResponse({"error": "invalid_description", "message": "customDescription too long"}, status_code=400)

    if not name or not transport or not url:
        return JSONResponse(
            {"error": "missing_required_fields", "message": "name, transport, and url are required."},
            status_code=400,
        )

    # Validations
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        return JSONResponse(
            {
                "error": "invalid_name",
                "message": "Name must be ^[a-zA-Z0-9_-]+$. Only alphanumeric characters, dashes, and underscores.",
            },
            status_code=400,
        )

    if transport not in ("http", "sse"):
        return JSONResponse(
            {"error": "invalid_transport", "message": "Transport must be 'http' or 'sse'."},
            status_code=400,
        )

    if not (url.startswith("http://") or url.startswith("https://")):
        return JSONResponse(
            {"error": "invalid_url", "message": "URL must start with http:// or https://"},
            status_code=400,
        )

    from core.context import MCP_SERVER_URL
    if MCP_SERVER_URL.startswith("https://") and not url.startswith("https://"):
        return JSONResponse(
            {"error": "https_required", "message": "HTTPS is required in production environments."},
            status_code=400,
        )

    try:
        row = await proxy_manager.add_proxy(
            name=name,
            transport=transport,
            url=url,
            auth_mode=auth_mode,
            bearer_token=bearer_token,
            oauth_config=oauth_config,
            client_secret=client_secret,
            custom_description=custom_description,
            workspace_label=workspace_label,
        )
        return JSONResponse(row)
    except ValueError as val_err:
        return JSONResponse({"error": "validation_error", "message": str(val_err)}, status_code=400)
    except Exception as e:
        logger.exception("add_proxy route error")
        return JSONResponse(
            {"error": "internal_error", "message": "An unexpected error occurred."},
            status_code=500,
        )


@http_route_registry.route(
    route="proxies",
    endpoint="{name}/oauth/start",
    methods=["POST"],
    name="start_proxy_oauth",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.proxy:write",),
    owner="api.proxy",
)
async def start_proxy_oauth_route(request: Request) -> Response:
    """Initiate OAuth 2.0 PKCE flow for a proxy and return authorization URL."""
    payload = await _validate_session_cookie(request)
    if payload is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    name = request.path_params.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "missing_name", "message": "Proxy name is required."}, status_code=400)

    try:
        proxy = await proxy_manager.get_proxy_by_name(name)
        if not proxy:
            return JSONResponse({"error": "not_found", "message": f"Proxy '{name}' not found."}, status_code=404)
        if proxy.get("authMode") != "oauth":
            return JSONResponse({"error": "invalid_mode", "message": f"Proxy '{name}' is not in oauth mode."}, status_code=400)
        oauth_config = proxy.get("oauthConfig")

        if not oauth_config:
            return JSONResponse({"error": "invalid_config", "message": "OAuth config is missing for this proxy."}, status_code=400)

        # Register synthetic manifest
        manifest = {
            "name": f"proxy_{name}",
            "version": "1.0.0",
            "tier": 1,
            "external_oauth": {
                "upstream": {
                    "authorize_url": oauth_config.get("authorize_url"),
                    "token_url": oauth_config.get("token_url"),
                    "client_id": oauth_config.get("client_id"),
                    "scopes": oauth_config.get("scopes", []),
                    "pkce": oauth_config.get("pkce", "S256"),
                    "redirect_path": "/oauth/plugin/upstream/callback",
                    "auth_header": oauth_config.get("auth_header", "Authorization")
                }
            }
        }
        
        from core.context import oauth_relay, MCP_SERVER_URL
        if not oauth_relay:
            return JSONResponse({"error": "oauth_relay_unavailable", "message": "OAuth relay is not initialized."}, status_code=500)

        await oauth_relay.upsert_manifest(f"proxy_{name}", manifest)

        # Build authorize URL
        authorize_url = await oauth_relay.build_authorize_url(
            plugin_id=f"proxy_{name}",
            provider="upstream",
            redirect_uri=f"{MCP_SERVER_URL}/oauth/plugin/upstream/callback",
            extra_context={"proxy_name": name}
        )
        return JSONResponse({"authorizeUrl": authorize_url})

    except Exception as e:
        logger.exception("start_proxy_oauth route error")
        return JSONResponse(
            {"error": "internal_error", "message": "An unexpected error occurred."},
            status_code=500,
        )


@http_route_registry.route(
    route="proxies",
    endpoint="{name}",
    methods=["PATCH"],
    name="update_proxy",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.proxy:write",),
    owner="api.proxy",
)
async def update_proxy_route(request: Request) -> Response:
    """Update an upstream proxy's custom description or workspace label."""
    payload = await _validate_session_cookie(request)
    if payload is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    name = request.path_params.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "missing_name", "message": "Proxy name is required."}, status_code=400)

    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("update_proxy: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    if "customDescription" not in body and "workspaceLabel" not in body:
        return JSONResponse(
            {"error": "missing_field", "message": "customDescription or workspaceLabel field is required."},
            status_code=400,
        )

    kwargs = {}
    if "customDescription" in body:
        custom_description = body.get("customDescription")
        if custom_description and len(custom_description) > MAX_PROXY_CUSTOM_DESCRIPTION_CHARS:
            return JSONResponse({"error": "invalid_description", "message": "customDescription too long"}, status_code=400)
        kwargs["custom_description"] = custom_description

    if "workspaceLabel" in body:
        workspace_label = body.get("workspaceLabel")
        kwargs["workspace_label"] = workspace_label

    try:
        row = await proxy_manager.update_proxy_description(name, **kwargs)
        return JSONResponse(row)
    except ValueError as val_err:
        return JSONResponse({"error": "validation_error", "message": str(val_err)}, status_code=400)
    except Exception as e:
        logger.exception("update_proxy route error")
        return JSONResponse(
            {"error": "internal_error", "message": "An unexpected error occurred."},
            status_code=500,
        )


@http_route_registry.route(
    route="proxies",
    endpoint="{name}",
    methods=["DELETE"],
    name="remove_proxy",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.proxy:write",),
    owner="api.proxy",
)
async def remove_proxy_route(request: Request) -> Response:
    """Unmount and delete an upstream proxy."""
    payload = await _validate_session_cookie(request)
    if payload is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    name = request.path_params.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "missing_name", "message": "Proxy name is required."}, status_code=400)

    try:
        result = await proxy_manager.remove_proxy(name)
        return JSONResponse(result)
    except ValueError as val_err:
        return JSONResponse({"error": "not_found", "message": str(val_err)}, status_code=404)
    except Exception as e:
        logger.exception("remove_proxy route error")
        return JSONResponse(
            {"error": "internal_error", "message": "An unexpected error occurred."},
            status_code=500,
        )


@http_route_registry.route(
    route="proxies",
    endpoint="{name}/test",
    methods=["POST"],
    name="test_proxy",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.proxy:write",),
    owner="api.proxy",
)
async def test_proxy_route(request: Request) -> Response:
    """Test and re-connect to an upstream proxy, updating its status."""
    payload = await _validate_session_cookie(request)
    if payload is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    name = request.path_params.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "missing_name", "message": "Proxy name is required."}, status_code=400)

    try:
        row = await proxy_manager.test_proxy(name)
        return JSONResponse(row)
    except ValueError as val_err:
        return JSONResponse({"error": "not_found", "message": str(val_err)}, status_code=404)
    except Exception as e:
        logger.exception("test_proxy route error")
        return JSONResponse(
            {"error": "internal_error", "message": "An unexpected error occurred."},
            status_code=500,
        )


@http_route_registry.route(
    route="proxies",
    endpoint="{name}/reindex",
    methods=["POST"],
    name="reindex_proxy",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.proxy:write",),
    owner="api.proxy",
)
async def reindex_proxy_route(request: Request) -> Response:
    """Re-snapshot, re-contribute, and re-embed routes for an upstream proxy."""
    payload = await _validate_session_cookie(request)
    if payload is None:
        return JSONResponse({"error": "unauthenticated"}, status_code=401)

    name = request.path_params.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "missing_name", "message": "Proxy name is required."}, status_code=400)

    retry_after = await _record_reindex_attempt_async(name)
    if retry_after is not None:
        return JSONResponse(
            {"error": "too_many_attempts", "retry_after": retry_after},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    try:
        provider = proxy_manager._active_handles.get(name)
        if not provider:
            return JSONResponse({"error": "not_found", "message": f"Proxy '{name}' is not mounted or active."}, status_code=404)

        # 1. Re-snapshot proxy tools
        tools = await provider.server.list_tools()

        # Force reindexing by removing existing database embeddings and jobs for this proxy
        await delete_plugin_route_embeddings(f"proxy_{name}")

        # 2. Re-contribute via event bus & 3. Enqueue route embedding jobs
        enqueued_count = 0
        try:
            row = await proxy_manager.get_proxy_by_name(name)
            custom_description = row.get("customDescription") if row else None
            workspace_label = row.get("workspaceLabel") if row else None

            proxy_manager._remove_proxy_routes(name)
            from core.plugin_loader.plugin_registry import get_registry
            registry = get_registry()
            registry.events.emit(
                "proxy.tools_discovered",
                {
                    "name": name,
                    "tools": tools,
                    "custom_description": custom_description,
                    "workspace_label": workspace_label,
                },
            )

            from core.context import route_registry
            from core_graph.worker import enqueue_pending
            enqueued_count = await enqueue_pending(route_registry)
        except Exception as queue_err:
            logger.warning("Event emit or route embedding enqueue failed, but DB deletes succeeded: %s", queue_err)

        return JSONResponse({
            "status": "ok",
            "message": f"Reindexed and enqueued {enqueued_count} tools for proxy '{name}'",
            "enqueued_count": enqueued_count
        })
    except Exception as e:
        logger.exception("reindex_proxy route error")
        return JSONResponse(
            {"error": "internal_error", "message": "An unexpected error occurred."},
            status_code=500,
        )
