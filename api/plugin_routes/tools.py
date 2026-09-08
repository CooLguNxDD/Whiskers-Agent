"""Plugin tools visibility REST handlers.

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


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools",
    methods=["GET"],
    name="api_get_plugin_tools",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:read",),
    owner="api.plugins",
)
async def get_plugin_tools(request: Request) -> Response:
    """Retrieves the list of tools provided by a specific plugin, including active/hidden states."""
    plugin_id = request.path_params.get("plugin_id", "")
    record = await _db_registry.get_by_id(plugin_id)
    if not record:
        return JSONResponse({"error": "plugin not found"}, status_code=404)
    capabilities = record.capabilities or []
    desc_map: dict[str, str] = {}
    try:
        all_tools = await mcp.list_tools()
        desc_map = {t.name: (t.description or "") for t in all_tools}
    except Exception as exc:
        logger.debug("swallowed exception (non-fatal): %s", exc)
    # Per-tool MCP-exposure state (tool_config). Sparse — absence = enabled.
    states: dict[str, bool] = {}
    hidden_states: dict[str, bool] = {}
    embedding_models: dict[str, str | None] = {}
    try:
        from db_layer.tool_config_store import get_tool_states, get_tool_hidden_states, get_tool_embedding_models
        states = await get_tool_states(plugin_id)
        hidden_states = await get_tool_hidden_states(plugin_id)
        embedding_models = await get_tool_embedding_models(plugin_id)
    except Exception as exc:
        logger.debug("swallowed exception (non-fatal): %s", exc)
    plugin_perms: dict[str, dict] = {}
    try:
        from db_layer.permission_store import get_plugin_permissions
        plugin_perms = await get_plugin_permissions(plugin_id)
    except Exception as exc:
        logger.debug("swallowed exception (non-fatal): %s", exc)

    # Retrieve from RouteRegistry (enabled + hidden routes)
    routes = []
    try:
        routes = get_registry().route_registry.routes_for_plugin(plugin_id)
    except Exception as exc:
        logger.debug("swallowed exception (non-fatal): %s", exc)

    prefix = f"{plugin_id}__"
    route_desc_map: dict[str, str] = {}
    route_names: set[str] = set()
    route_method_map: dict[str, str] = {}
    route_group_map: dict[str, str] = {}
    for r in routes:
        raw_name = r.operation_id[len(prefix):] if r.operation_id.startswith(prefix) else r.operation_id
        route_names.add(raw_name)
        route_desc_map[raw_name] = r.description or ""
        route_method_map[raw_name] = getattr(r, "method", "")

        # Determine group (category)
        group_val = "general"
        tags = getattr(r, "tags", None)
        if tags and isinstance(tags, (list, tuple)) and len(tags) > 0:
            group_val = tags[0]
        else:
            meta = getattr(r, "meta", None)
            if isinstance(meta, dict) and "category" in meta:
                group_val = meta["category"]
            elif hasattr(r, "meta") and hasattr(r.meta, "category"):
                group_val = getattr(r.meta, "category")
        route_group_map[raw_name] = group_val

    # Build the full raw-name set as the union
    tool_names_set = route_names | set(states.keys()) | set(hidden_states.keys()) | set(capabilities)
    tool_names = sorted(list(tool_names_set))

    from core_graph.node.helpers.routing import operation_class

    tools = []
    for name in tool_names:
        desc = route_desc_map.get(name)
        if desc is None:
            desc = desc_map.get(name, "")

        method_val = route_method_map.get(name, "")
        access_val = operation_class({"method": method_val})
        group_val = route_group_map.get(name, "general")

        tools.append({
            "name": name,
            "description": desc,
            "is_enabled": states.get(name, True),
            "is_hidden": hidden_states.get(name, False),
            "embedding_model": embedding_models.get(name, None),
            "permission": plugin_perms.get(name) or None,
            "group": group_val,
            "access": access_val,
        })

    return JSONResponse({"plugin_id": plugin_id, "tools": tools})


async def _set_plugin_tools_hidden(request: Request, hidden: bool) -> Response:
    """Toggle gateway hidden state for ALL of a plugin's tools.

    Persists ``meta.tools_hidden`` on the plugin and applies the FastMCP
    transform live to each capability (allowlisted tools are skipped). Hidden
    tools remain reachable internally by run_graph.
    """
    plugin_id = request.path_params.get("plugin_id", "")
    record = await _db_registry.get_by_id(plugin_id)
    if not record:
        return JSONResponse({"error": "plugin not found"}, status_code=404)

    new_meta = dict(record.meta or {})
    new_meta["tools_hidden"] = hidden
    try:
        await _db_registry.update_plugin_meta(plugin_id, new_meta)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"plugin tools-hidden persist {plugin_id} -> {hidden}")

    # Find all actual tools for this plugin (union of registry routes and capabilities)
    tool_names = set(record.capabilities or [])
    try:
        routes = get_registry().route_registry.routes_for_plugin(plugin_id)
        prefix = f"{plugin_id}__"
        for r in routes:
            raw_name = r.operation_id[len(prefix):] if r.operation_id.startswith(prefix) else r.operation_id
            tool_names.add(raw_name)
    except Exception as exc:
        logger.warning("tools-hidden toggle route discovery failed for %s: %s", plugin_id, exc)

    applied_hidden = 0
    applied_shown = 0
    from db_layer.tool_config_store import set_tool_hidden
    from core.proxy_tools.tool_visibility import GATEWAY_ALWAYS_VISIBLE, proxy_exposed_name
    from db_layer.gateway_settings_store import get_gateway_unified
    unified_on = False
    try:
        unified_on = await get_gateway_unified()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("hide-tools: get_gateway_unified failed", exc_info=True)

    for tn in tool_names:
        await set_tool_hidden(plugin_id, tn, hidden)
        exposed_cap = proxy_exposed_name(plugin_id, tn)
        if hidden:
            if tool_visibility.hide_gateway(exposed_cap):
                applied_hidden += 1
        else:
            # Clear selective flag in DB; only restore MCP exposure when unified is OFF.
            if unified_on and exposed_cap not in GATEWAY_ALWAYS_VISIBLE:
                continue
            if tool_visibility.show_gateway(exposed_cap):
                applied_shown += 1

    # Refresh summary after bulk hide/show (catalog visibility changed)
    try:
        from db_layer.gateway_settings_store import refresh_tools_summary
        await refresh_tools_summary()
    except Exception as exc:
        logger.warning("refresh after hide-tools %s: %s", plugin_id, exc)

    return JSONResponse(
        {"plugin_id": plugin_id, "tools_hidden": hidden, "applied": applied_hidden if hidden else applied_shown},
        status_code=200,
    )


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/hide-tools",
    methods=["POST"],
    name="api_hide_plugin_tools",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def hide_plugin_tools(request: Request) -> Response:
    """Gateway-hide every tool in a plugin (meta.tools_hidden = TRUE)."""
    return await _set_plugin_tools_hidden(request, hidden=True)


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/hide-tools",
    methods=["DELETE"],
    name="api_show_plugin_tools",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def show_plugin_tools(request: Request) -> Response:
    """Restore every gateway-hidden tool in a plugin (meta.tools_hidden = FALSE)."""
    return await _set_plugin_tools_hidden(request, hidden=False)


# ---------------------------------------------------------------------------
# Skills (DB-persisted workflow docs for planner injection)
# GET    /api/plugins/{plugin_id}/skills
# PUT    /api/plugins/{plugin_id}/skills          body: {key, content}
# DELETE /api/plugins/{plugin_id}/skills          body: {key}
# POST   /api/plugins/{plugin_id}/skills/reload   body: {key?}  — load from disk → DB
# ---------------------------------------------------------------------------

