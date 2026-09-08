"""




Route-embeddings management REST API.

Exposes per-plugin enable/disable of route embeddings and a read endpoint
for the admin UI.  Mirrors the plugin toggle pattern in plugin_routes.py.

Endpoints
---------
GET    /api/routes/session_gated                                 — List route-embedding groups.
POST   /api/routes/session_gated/reindex                         — Trigger a full route re-indexing of all contributed tools/routes.
DELETE /api/routes/session_gated/{plugin_id}/enable              — Set is_enabled=FALSE for all route_embeddings rows of a plugin.
POST   /api/routes/session_gated/{plugin_id}/enable              — Set is_enabled=TRUE for all route_embeddings rows of a plugin.
DELETE /api/routes/session_gated/{plugin_id}/{route_id}/enable   — Set is_enabled=FALSE for a single route_embeddings row of a plugin.
POST   /api/routes/session_gated/{plugin_id}/{route_id}/enable   — Set is_enabled=TRUE for a single route_embeddings row of a plugin.
"""

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from utils.error_response import safe_error_response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from db_layer import route_store

logger = logging.getLogger("whiskers")


def _canonical_plugin_id(plugin_id: str) -> str:
    """Ensure the plugin_id matches the stored format.

    The database stores the short/normalized plugin name (e.g., 'portfolio_plugin').
    Ensure any input (whether full package path or short name) is normalized to
    the short name.
    """
    if not plugin_id:
        return plugin_id
    if "." in plugin_id:
        return plugin_id.rsplit(".", 1)[-1]
    return plugin_id


# ---------------------------------------------------------------------------
# GET /api/routes
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="routes",
    endpoint="",
    methods=["GET"],
    name="api_list_routes",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:config:read",),
    owner="api.route",
)
async def list_routes(request: Request) -> Response:
    """List route-embedding groups.

    Without ``?plugin_id``: returns one aggregate row per plugin with counts.
    With ``?plugin_id=<id>``: returns individual route rows for that plugin.
    """
    plugin_id = _canonical_plugin_id(request.query_params.get("plugin_id", "").strip())
    limit_param = request.query_params.get("limit")
    offset_param = request.query_params.get("offset")
    try:
        limit = int(limit_param) if limit_param is not None else 200
    except ValueError:
        limit = 200
    try:
        offset = int(offset_param) if offset_param is not None else 0
    except ValueError:
        offset = 0

    logger.info("Listing route embeddings for plugin_id=%s (limit=%d, offset=%d)", plugin_id or "ALL", limit, offset)    
    try:
        if plugin_id:
            rows = await route_store.list_routes_for_plugin(plugin_id, limit=limit, offset=offset)
            routes = [
                {
                    "id": r.id,
                    "operation_id": r.operation_id,
                    "method": r.method,
                    "path_template": r.path_template,
                    "description": r.description,
                    "is_enabled": r.is_enabled,
                    "is_fast_path": r.is_fast_path,
                    "embedded_at": r.embedded_at.isoformat() if r.embedded_at else None,
                }
                for r in rows
            ]
            return JSONResponse({"plugin_id": plugin_id, "routes": routes})

        rows = await route_store.list_route_groups()
        groups = [
            {
                "plugin_id": r.plugin_id,
                "route_count": r.route_count,
                "enabled_count": r.enabled_count,
                "all_enabled": bool(r.all_enabled) if r.all_enabled is not None else True,
                "first_embedded_at": r.first_embedded_at.isoformat() if r.first_embedded_at else None,
            }
            for r in rows
        ]
        return JSONResponse({"routes": groups})

    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/routes GET")


# ---------------------------------------------------------------------------
# POST /api/routes/{plugin_id}/enable
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="routes",
    endpoint="{plugin_id}/enable",
    methods=["POST"],
    name="api_enable_plugin_routes",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:config:write",),
    owner="api.route",
)
async def enable_plugin_routes(request: Request) -> Response:
    """Set is_enabled=TRUE for all route_embeddings rows of a plugin."""
    plugin_id = _canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)
    
    logger.info("Enabling routes for plugin %s", plugin_id)

    try:
        updated = await route_store.set_plugin_routes_enabled(plugin_id, True)

        if updated == 0:
            return JSONResponse(
                {"error": "no routes found for plugin_id", "plugin_id": plugin_id},
                status_code=404,
            )

        logger.info("Enabled %d routes for plugin %s", updated, plugin_id)
        return JSONResponse({"plugin_id": plugin_id, "enabled": True, "updated_count": updated})

    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/routes/{plugin_id}/enable POST")


# ---------------------------------------------------------------------------
# DELETE /api/routes/{plugin_id}/enable
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="routes",
    endpoint="{plugin_id}/enable",
    methods=["DELETE"],
    name="api_disable_plugin_routes",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:config:write",),
    owner="api.route",
)
async def disable_plugin_routes(request: Request) -> Response:
    """Set is_enabled=FALSE for all route_embeddings rows of a plugin."""
    plugin_id = _canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)

    logger.info("Disabling routes for plugin %s", plugin_id)
    try:
        updated = await route_store.set_plugin_routes_enabled(plugin_id, False)

        if updated == 0:
            return JSONResponse(
                {"error": "no routes found for plugin_id", "plugin_id": plugin_id},
                status_code=404,
            )

        logger.info("Disabled %d routes for plugin %s", updated, plugin_id)
        return JSONResponse({"plugin_id": plugin_id, "enabled": False, "updated_count": updated})

    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/routes/{plugin_id}/enable DELETE")


# ---------------------------------------------------------------------------
# POST /api/routes/{plugin_id}/{route_id}/enable
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="routes",
    endpoint="{plugin_id}/{route_id}/enable",
    methods=["POST"],
    name="api_enable_route",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:config:write",),
    owner="api.route",
)
async def enable_route(request: Request) -> Response:
    """Set is_enabled=TRUE for a single route_embeddings row of a plugin."""
    plugin_id = _canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    try:
        route_id = int(request.path_params.get("route_id", "0"))
    except ValueError:
        return JSONResponse({"error": "invalid route_id"}, status_code=400)

    if not plugin_id or not route_id:
        return JSONResponse({"error": "missing plugin_id or route_id"}, status_code=400)

    logger.info("Enabling route %d for plugin %s", route_id, plugin_id)

    try:
        updated = await route_store.set_route_enabled(plugin_id, route_id, True)

        if updated == 0:
            return JSONResponse(
                {"error": "route not found", "plugin_id": plugin_id, "route_id": route_id},
                status_code=404,
            )

        logger.info("Enabled route %d for plugin %s", route_id, plugin_id)
        return JSONResponse({"plugin_id": plugin_id, "id": route_id, "enabled": True})

    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/routes/{plugin_id}/{route_id}/enable POST")


# ---------------------------------------------------------------------------
# DELETE /api/routes/{plugin_id}/{route_id}/enable
# ---------------------------------------------------------------------------

@http_route_registry.route(
    route="routes",
    endpoint="{plugin_id}/{route_id}/enable",
    methods=["DELETE"],
    name="api_disable_route",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:config:write",),
    owner="api.route",
)
async def disable_route(request: Request) -> Response:
    """Set is_enabled=FALSE for a single route_embeddings row of a plugin."""
    plugin_id = _canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    try:
        route_id = int(request.path_params.get("route_id", "0"))
    except ValueError:
        return JSONResponse({"error": "invalid route_id"}, status_code=400)

    if not plugin_id or not route_id:
        return JSONResponse({"error": "missing plugin_id or route_id"}, status_code=400)

    logger.info("Disabling route %d for plugin %s", route_id, plugin_id)

    try:
        updated = await route_store.set_route_enabled(plugin_id, route_id, False)

        if updated == 0:
            return JSONResponse(
                {"error": "route not found", "plugin_id": plugin_id, "route_id": route_id},
                status_code=404,
            )

        logger.info("Disabled route %d for plugin %s", route_id, plugin_id)
        return JSONResponse({"plugin_id": plugin_id, "id": route_id, "enabled": False})

    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"api/routes/{plugin_id}/{route_id}/enable DELETE")


@http_route_registry.route(
    route="routes",
    endpoint="reindex",
    methods=["POST"],
    name="api_reindex_all_routes",
    auth_policy=AuthPolicy.SESSION_GATED,
    required_scopes=("core:config:write",),
    owner="api.route",
)
async def reindex_all_routes(request: Request) -> Response:
    """Trigger a full route re-indexing of all contributed tools/routes."""
    logger.info("Triggering full route re-indexing")
    try:
        from core.context import route_registry
        from core_graph.worker import enqueue_pending
        
        # Enqueue route embedding jobs for all routes currently in the registry
        await enqueue_pending(route_registry)
        
        return JSONResponse({"status": "ok", "message": f"Successfully triggered re-indexing for {len(route_registry)} routes"})
    except Exception as exc:
        return safe_error_response(exc, log_ctx="api/routes/reindex POST")
