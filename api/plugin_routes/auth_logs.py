"""OAuth, credentials, aliases, reindex.

Part of the ``api.plugin_routes`` package (split for maintainability).
"""
from __future__ import annotations

import asyncio
import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from utils.error_response import safe_error_response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from db_layer.plugin_registry_store import PluginMetaKey
from db_layer.telemetry_store import telemetry_store
from core.plugin_loader.config_file_store import load_base_config
from core.plugin_loader.skill_file_store import skill_content_hash

from api.plugin_routes import deps as _deps
from api.plugin_routes.deps import (  # noqa: F401 — re-exported names used below
    mcp,
    oauth_relay,
    vault,
    tool_visibility,
    get_async_session,
    get_registry,
    _plugin_loader,
    load_skill_from_disk,
    _db_registry,
    _refresh_live_plugin_skills,
    _skill_row_for,
    parse_tier,
    _build_plugin_payload,
)

logger = logging.getLogger("whiskers")

# DELETE /api/plugins/{plugin_id}/oauth/{provider}
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/oauth/{provider}",
    methods=["DELETE"],
    name="api_revoke_plugin_oauth",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def revoke_plugin_oauth(request: Request) -> Response:
    """Revoke the Layer 2 OAuth token for a plugin/provider pair."""
    plugin_id = request.path_params.get("plugin_id", "")
    provider = request.path_params.get("provider", "")

    if not plugin_id or not provider:
        return JSONResponse({"error": "missing plugin_id or provider"}, status_code=400)

    if oauth_relay is None:
        return JSONResponse(
            {"error": "ExternalOAuthRelay not configured (DATABASE_URL missing)"},
            status_code=503,
        )

    try:
        await oauth_relay.revoke_token(plugin_id, provider)
        return JSONResponse(
            {"id": plugin_id, "provider": provider, "status": "revoked"},
            status_code=200,
        )
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/plugins/{plugin_id}/oauth/{provider} DELETE")


# ---------------------------------------------------------------------------
# /api/mods aliases (backward-compat for pre-rebuild frontend bundles)
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="mods",
    endpoint="",
    methods=["GET"],
    name="api_list_mods_alias",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:read",),
    owner="api.plugins",
)
async def list_mods_alias(request: Request) -> Response:
    """Legacy alias for /api/plugins."""
    from api.plugin_routes.registry import list_plugins
    return await list_plugins(request)


@http_route_registry.route(
    route="mods",
    endpoint="{plugin_id}/enable",
    methods=["POST"],
    name="api_enable_mod_alias",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def enable_mod_alias(request: Request) -> Response:
    """Legacy alias to enable a plugin."""
    from api.plugin_routes.registry import enable_plugin
    return await enable_plugin(request)


@http_route_registry.route(
    route="mods",
    endpoint="{plugin_id}/enable",
    methods=["DELETE"],
    name="api_disable_mod_alias",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def disable_mod_alias(request: Request) -> Response:
    """Legacy alias to disable a plugin."""
    from api.plugin_routes.registry import disable_plugin
    return await disable_plugin(request)


# ---------------------------------------------------------------------------
# Direct Credentials Endpoints
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/direct-credentials",
    methods=["PUT"],
    name="api_set_plugin_direct_credentials",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def set_plugin_direct_credentials(request: Request) -> Response:
    """Set username and password in the Vault for direct auth."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid json body", "message": "Expected a JSON object"}, status_code=400)

    username = body.get("username", "")
    password = body.get("password", "")
    api_token = body.get("api_token", "")

    if not api_token and (not username or not password):
        return JSONResponse({"error": "api_token or username/password are required"}, status_code=400)

    try:
        db_record = await _db_registry.get_by_id(plugin_id)
        if db_record is None:
            return JSONResponse({"error": "plugin not found"}, status_code=404)
    except Exception as exc:
        logger.warning("direct-credentials: DB error checking plugin: %s", exc)

    try:
        # set in vault
        if api_token:
            await vault.set(plugin_id, "api_token", api_token)
            await vault.delete(plugin_id, "username")
            await vault.delete(plugin_id, "password")
        if username and password:
            await vault.set(plugin_id, "username", username)
            await vault.set(plugin_id, "password", password)
            await vault.delete(plugin_id, "api_token")

        registry = get_registry()
        # clear direct session to force fresh login
        await registry.auth._vault_clear_direct_token(plugin_id)
        
        # clear needs_reauth
        registry.auth.clear_needs_reauth(plugin_id)

        # refresh auth headers to verify credentials
        headers = await registry.auth.refresh_auth_headers(plugin_id)

        auth_status = registry.auth.get_auth_status(plugin_id)
        has_auth = any(k.lower() in ("authorization", "authentication") for k in headers.keys())
        auth_status_str = auth_status.value if hasattr(auth_status, "value") else str(auth_status)
        if has_auth and auth_status_str == "ok":
            return JSONResponse({"status": "ok", "auth_status": "ok"}, status_code=200)
        else:
            registry.auth.mark_needs_reauth(plugin_id)
            return JSONResponse({"status": "auth_failed", "auth_status": "needs_reauth"}, status_code=200)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/plugins/{plugin_id}/direct-credentials PUT")


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/direct-credentials",
    methods=["DELETE"],
    name="api_delete_plugin_direct_credentials",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def delete_plugin_direct_credentials(request: Request) -> Response:
    """Delete username and password from the Vault for direct auth."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)

    try:
        db_record = await _db_registry.get_by_id(plugin_id)
        if db_record is None:
            return JSONResponse({"error": "plugin not found"}, status_code=404)
    except Exception as exc:
        logger.warning("direct-credentials: DB error checking plugin: %s", exc)

    try:
        await vault.delete(plugin_id, "username")
        await vault.delete(plugin_id, "password")
        await vault.delete(plugin_id, "api_token")

        registry = get_registry()
        await registry.auth._vault_clear_direct_token(plugin_id)
        registry.auth.mark_needs_reauth(plugin_id)
        await registry.auth.refresh_auth_headers(plugin_id)

        return JSONResponse({"status": "ok"}, status_code=200)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/plugins/{plugin_id}/direct-credentials DELETE")


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/reindex",
    methods=["POST"],
    name="api_reindex_plugin",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def reindex_plugin(request: Request) -> Response:
    """Re-snapshot, re-contribute, and re-embed routes for a specific plugin."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)

    try:
        # Check if plugin exists (in-memory registry or database)
        registry = get_registry()
        plugin = next((p for p in registry.lifecycle._plugins if getattr(p, "name", "") == plugin_id), None)
        if not plugin:
            db_record = await _db_registry.get_by_id(plugin_id)
            if not db_record:
                return JSONResponse({"error": "not_found", "message": f"Plugin '{plugin_id}' not found."}, status_code=404)

        from core.context import route_registry
        from core_graph.worker.job_producer import enqueue_pending

        # Force reindexing by removing existing database embeddings and jobs for this plugin
        await _db_registry.delete_plugin_route_embeddings(plugin_id)

        # Enqueue the pending jobs in the RouteRegistry
        enqueued_count = await enqueue_pending(route_registry)

        return JSONResponse({
            "status": "ok",
            "message": f"Reindexed and enqueued {enqueued_count} routes for plugin '{plugin_id}'"
        }, status_code=200)

    except Exception as exc:
        logger.exception("api/plugins/%s/reindex POST: %s", plugin_id, exc)
        return JSONResponse(
            {"error": "internal_error", "message": "An unexpected error occurred."},
            status_code=500,
        )

