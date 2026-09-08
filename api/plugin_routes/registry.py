"""Plugin registry lifecycle REST handlers.

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

# ---------------------------------------------------------------------------
# GET /api/plugins
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="",
    methods=["GET"],
    name="api_list_plugins",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:read",),
    owner="api.plugins",
)
async def list_plugins(request: Request) -> Response:
    """Return all loaded plugins with their DB metadata and Layer 2 OAuth status."""
    try:
        registry = get_registry()
    except RuntimeError:
        return JSONResponse({"plugins": [], "system_tier": 1, "system_tier_name": "LITE"}, status_code=200)

    plugins = registry.lifecycle._plugins  # in-memory authoritative list

    # Fetch all DB records keyed by plugin id
    try:
        db_records = await _db_registry.get_all()
        db_map = {r.id: r for r in db_records}
    except Exception as exc:
        logger.warning("api/plugins: DB unavailable, serving in-memory list only: %s", exc)
        db_map = {}

    loaded_map = {getattr(p, "name", ""): p for p in plugins}
    result = []
    seen = set()

    # Iterate over DB records so inactive/disabled plugins are returned
    for record_id, record in db_map.items():
        seen.add(record_id)
        plugin = loaded_map.get(record_id)
        if plugin is None:
            tier_str = record.meta.get("tier", "free") if record else "free"
            class _Ghost:
                """Ghost class representation for unmounted/inactive plugins registered in the DB."""
                name = record_id
                version = record.version if record else "0.0.0"
                tier = parse_tier(tier_str)
            plugin = _Ghost()

        # Determine Layer 2 OAuth status per provider
        oauth_status: dict[str, str] = {}
        providers: list[str] = []
        if record is not None:
            providers = record.external_oauth_providers or []

        if oauth_relay is not None and providers:
            for provider in providers:
                try:
                    connected = await oauth_relay.has_token(record_id, provider)
                    oauth_status[provider] = "connected" if connected else "not_connected"
                except Exception as exc:
                    logger.debug(
                        "api/plugins: has_token failed plugin=%s provider=%s: %s",
                        record_id, provider, exc,
                    )
                    oauth_status[provider] = "not_connected"

        result.append(_build_plugin_payload(plugin, record, oauth_status))

    # Append any in-memory loaded plugins not present in DB
    for name, plugin in loaded_map.items():
        if name not in seen:
            result.append(_build_plugin_payload(plugin, None, {}))

    try:
        from core.plugin_loader.plugin_registry import Tier
        system_tier_name = Tier(registry.system_tier).name
    except (ValueError, ImportError):
        system_tier_name = str(registry.system_tier)

    return JSONResponse({
        "plugins": result,
        "system_tier": registry.system_tier,
        "system_tier_name": system_tier_name
    }, status_code=200)


# ---------------------------------------------------------------------------
# DELETE /api/plugins/{plugin_id}  (hard-delete stale/inactive — not /enable)
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}",
    methods=["DELETE"],
    name="api_delete_plugin",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def delete_plugin(request: Request) -> Response:
    """Hard-delete a stale or inactive plugins row.

    Distinct from ``DELETE .../enable`` (soft-disable). Returns 404 when no
    matching DB record exists. If the plugin is actively mounted in the
    running process, tear it down first before deleting its record.
    """
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)

    try:
        record = await _db_registry.get_by_id(plugin_id)
        if record is None:
            return JSONResponse({"error": "plugin not found"}, status_code=404)

        try:
            registry = get_registry()
            if hasattr(registry, "lifecycle"):
                is_live = False
                if hasattr(registry.lifecycle, "is_registered"):
                    is_live = registry.lifecycle.is_registered(plugin_id)
                elif hasattr(registry.lifecycle, "_plugin_id_map"):
                    is_live = plugin_id in (registry.lifecycle._plugin_id_map or {})
                if is_live:
                    return JSONResponse(
                        {"error": "cannot delete live plugin", "plugin_id": plugin_id},
                        status_code=409,
                    )
        except RuntimeError:
            pass

        if plugin_id.startswith("proxy_"):
            proxy_name = plugin_id[len("proxy_"):]
            try:
                from core.proxy.proxy_manager import proxy_manager
                await proxy_manager.remove_proxy(proxy_name)
            except ValueError:
                await _db_registry.delete_plugin(plugin_id)
        else:
            await _db_registry.delete_plugin(plugin_id)
        return JSONResponse({"id": plugin_id, "deleted": True}, status_code=200)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/plugins DELETE {plugin_id}")


# ---------------------------------------------------------------------------
# POST /api/plugins/prune-stale
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="prune-stale",
    methods=["POST"],
    name="api_prune_stale_plugins",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def prune_stale_plugins(request: Request) -> Response:
    """Bulk-delete all ``meta.stale`` plugin rows that are not currently live."""
    try:
        records = await _db_registry.get_all()
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/plugins/prune-stale: list failed")

    live_ids: set[str] = set()
    try:
        registry = get_registry()
        live_ids = set((getattr(registry.lifecycle, "_plugin_id_map", {}) or {}).keys())
    except RuntimeError:
        pass

    pruned = 0
    errors: list[str] = []
    for record in records:
        if not (record.meta or {}).get(PluginMetaKey.STALE.value):
            continue
        if record.id in live_ids:
            continue
        try:
            if record.id.startswith("proxy_"):
                proxy_name = record.id[len("proxy_"):]
                try:
                    from core.proxy.proxy_manager import proxy_manager
                    await proxy_manager.remove_proxy(proxy_name)
                except ValueError:
                    await _db_registry.delete_plugin(record.id)
            else:
                await _db_registry.delete_plugin(record.id)
            pruned += 1
        except Exception as exc:
            logger.warning("prune-stale: failed for %s: %s", record.id, exc)
            errors.append(f"{record.id}: {exc}")

    body: dict = {"pruned": pruned}
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=200)


# ---------------------------------------------------------------------------
# POST /api/plugins/{plugin_id}/enable
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/enable",
    methods=["POST"],
    name="api_enable_plugin",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def enable_plugin(request: Request) -> Response:
    """Set is_active=True for the given plugin in the DB."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)

    try:
        await _db_registry.set_active(plugin_id, is_active=True)
        
        lifecycle_warning = None
        try:
            await get_registry().lifecycle.reinitialize_plugin(plugin_id)
        except Exception as lw_exc:
            logger.warning(f"Lifecycle warning enabling {plugin_id}: {lw_exc}")
            lifecycle_warning = str(lw_exc)

        record = await _db_registry.get_by_id(plugin_id)
        if record and record.capabilities:
            for cap in record.capabilities:
                try:
                    mcp.enable(names={cap})
                except Exception as exc:
                    logger.warning("Failed to enable capability '%s' for plugin %s: %s", cap, plugin_id, exc)

        # Refresh live tools summary in instructions
        try:
            from db_layer.gateway_settings_store import refresh_tools_summary
            await refresh_tools_summary()
        except Exception as exc:
            logger.warning("refresh after enable %s: %s", plugin_id, exc)

        return JSONResponse({"id": plugin_id, "enabled": True, "lifecycle_warning": lifecycle_warning}, status_code=200)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/plugins/{plugin_id}/enable POST")


# ---------------------------------------------------------------------------
# DELETE /api/plugins/{plugin_id}/enable
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/enable",
    methods=["DELETE"],
    name="api_disable_plugin",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:write",),
    owner="api.plugins",
)
async def disable_plugin(request: Request) -> Response:
    """Set is_active=False for the given plugin in the DB."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)

    try:
        await _db_registry.set_active(plugin_id, is_active=False)
        
        record = await _db_registry.get_by_id(plugin_id)
        if record and record.capabilities:
            for cap in record.capabilities:
                try:
                    mcp.disable(names={cap})
                except Exception as exc:
                    logger.warning("Failed to disable capability '%s' for plugin %s: %s", cap, plugin_id, exc)                    
        lifecycle_warning = None
        try:
            await get_registry().lifecycle.teardown_plugin(plugin_id)
        except Exception as lw_exc:
            logger.warning(f"Lifecycle warning disabling {plugin_id}: {lw_exc}")
            lifecycle_warning = str(lw_exc)

        try:
            from db_layer.gateway_settings_store import refresh_tools_summary
            await refresh_tools_summary()
        except Exception as exc:
            logger.warning("refresh after disable %s: %s", plugin_id, exc)

        return JSONResponse({"id": plugin_id, "enabled": False, "lifecycle_warning": lifecycle_warning}, status_code=200)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/plugins/{plugin_id}/enable DELETE")


# ---------------------------------------------------------------------------
# GET /api/plugins/{plugin_id}
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}",
    methods=["GET"],
    name="api_get_plugin",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:read",),
    owner="api.plugins",
)
async def get_plugin(request: Request) -> Response:
    """Retrieves the details of a specific plugin by its ID."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)
        
    try:
        record = await _db_registry.get_by_id(plugin_id)
        if record is None:
            return JSONResponse({"error": "plugin not found"}, status_code=404)
            
        oauth_status: dict[str, str] = {}
        providers = record.external_oauth_providers or []
        if oauth_relay is not None and providers:
            for provider in providers:
                try:
                    connected = await oauth_relay.has_token(plugin_id, provider)
                    oauth_status[provider] = "connected" if connected else "not_connected"
                except Exception as exc:
                    logger.warning("Failed to check oauth status for plugin %s, provider %s: %s", plugin_id, provider, exc)
                    oauth_status[provider] = "not_connected"
                    
        plugin = None
        try:
            registry = get_registry()
            plugin = registry.lifecycle._plugin_id_map.get(plugin_id)
        except RuntimeError:
            pass
            
        if not plugin:
            tier_str = record.meta.get("tier", "free") if record else "free"
            class MockPlugin:
                """MockPlugin class representation for plugins that are not loaded in the runtime context."""
                name = record.id
                version = record.version
                tier = parse_tier(tier_str)
            plugin = MockPlugin()
            
        return JSONResponse(_build_plugin_payload(plugin, record, oauth_status), status_code=200)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/plugins/{plugin_id} GET")


# ---------------------------------------------------------------------------
# GET /api/plugins/{plugin_id}/health
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/health",
    methods=["GET"],
    name="api_get_plugin_health",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:whiskers.plugins:read",),
    owner="api.plugins",
)
async def get_plugin_health(request: Request) -> Response:
    """Checks the health of an active plugin using its internal registry probe."""
    plugin_id = request.path_params.get("plugin_id", "")
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)
        
    try:
        record = await _db_registry.get_by_id(plugin_id)
        if record is None:
            return JSONResponse({"error": "plugin not found"}, status_code=404)
            
        missing = await vault.missing_keys(plugin_id, record.required_credentials or [])
        credentials_present = len(missing) == 0
        
        oauth_status: dict[str, str] = {}
        providers = record.external_oauth_providers or []
        if oauth_relay is not None and providers:
            for provider in providers:
                try:
                    connected = await oauth_relay.has_token(plugin_id, provider)
                    oauth_status[provider] = "connected" if connected else "not_connected"
                except Exception as exc:
                    logger.warning("Failed to check oauth status for plugin %s, provider %s: %s", plugin_id, provider, exc)
                    oauth_status[provider] = "not_connected"
                    
        capabilities = record.capabilities or []
        states: dict[str, bool] = {}
        hidden_states: dict[str, bool] = {}
        try:
            from db_layer.tool_config_store import get_tool_states, get_tool_hidden_states
            states = await get_tool_states(plugin_id)
            hidden_states = await get_tool_hidden_states(plugin_id)
        except Exception as exc:
            logger.debug("swallowed exception (non-fatal): %s", exc)

        routes = []
        try:
            routes = get_registry().route_registry.routes_for_plugin(plugin_id)
        except Exception as exc:
            logger.debug("swallowed exception (non-fatal): %s", exc)

        prefix = f"{plugin_id}__"
        route_names: set[str] = set()
        for r in routes:
            raw_name = r.operation_id[len(prefix):] if r.operation_id.startswith(prefix) else r.operation_id
            route_names.add(raw_name)

        tools_count = len(route_names | set(states.keys()) | set(hidden_states.keys()) | set(capabilities))
        
        lifecycle_state = "unloaded"
        try:
            registry = get_registry()
            if plugin_id in registry.lifecycle._plugin_id_map:
                lifecycle_state = "loaded"
        except RuntimeError:
            pass
            
        return JSONResponse({
            "is_active": record.is_active,
            "oauth_status": oauth_status,
            "credentials_present": credentials_present,
            "tools_count": tools_count,
            "lifecycle_state": lifecycle_state
        }, status_code=200)
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/plugins/{plugin_id}/health GET")


# ---------------------------------------------------------------------------
# GET /api/plugins/{plugin_id}/tools
# ---------------------------------------------------------------------------
