"""




Per-tool MCP-exposure management REST API.

Toggles whether an individual ``@mcp.tool()`` is visible/callable by MCP
clients. Persists to the ``tool_config`` table (core_017) and applies the
change live via ``tool_visibility`` (FastMCP disable/enable transform) so no
restart is needed. Distinct from ``api/route_routes.py``, which toggles
semantic-search routing (``route_embeddings.is_enabled``).

Endpoints
---------
POST   /api/plugins/session_gated/{plugin_id}/tools/batch-state                   — Sets state and/or permission for a batch of tools in a plugin.
POST   /api/plugins/session_gated/{plugin_id}/tools/{tool_name}/embedding-model   — Set the embedding model configured for a tool.
DELETE /api/plugins/session_gated/{plugin_id}/tools/{tool_name}/enable            — Hide a single tool from MCP clients (tool_config.is_enabled = FALSE).
POST   /api/plugins/session_gated/{plugin_id}/tools/{tool_name}/enable            — Expose a single tool to MCP clients (tool_config.is_enabled = TRUE).
DELETE /api/plugins/session_gated/{plugin_id}/tools/{tool_name}/hide              — Restore a gateway-hidden tool to MCP exposure (tool_config.is_hidden = FALSE).
POST   /api/plugins/session_gated/{plugin_id}/tools/{tool_name}/hide              — Gateway-hide a single tool (tool_config.is_hidden = TRUE). Still run-able by run_graph.
GET    /api/plugins/session_gated/{plugin_id}/tools/{tool_name}/permission        — Gets the security permissions for a specific tool (read/write/confirm).
POST   /api/plugins/session_gated/{plugin_id}/tools/{tool_name}/permission        — Updates the security permissions for a specific tool (read/write/confirm).
POST   /api/plugins/session_gated/{plugin_id}/tools/{tool_name}/state             — Sets a tool's state unified: enabled, hidden, or disabled.
"""

import asyncio
import logging
from enum import Enum

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from utils.error_response import safe_error_response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from core.context import mcp, tool_visibility
from db_layer.tool_config_store import set_tool_enabled, set_tool_hidden, set_tool_embedding_model
from db_layer.permission_store import get_permission, set_permission, get_plugin_permissions
from db_layer.gateway_settings_store import refresh_tools_summary
from utils.plugin_ids import canonical_plugin_id

logger = logging.getLogger("whiskers")


class ToolState(str, Enum):
    """Enumeration of unified tool visibility states: ENABLED, HIDDEN, or DISABLED."""
    ENABLED = "enabled"
    HIDDEN = "hidden"
    DISABLED = "disabled"


async def _apply_tool_state(plugin_id: str, tool_name: str, state: ToolState) -> tuple[bool, bool]:
    """Helper to apply database and live visibility state updates for a tool (no reinit/refresh)."""
    is_enabled = False
    is_hidden = False

    if state == ToolState.ENABLED:
        is_enabled = True
        is_hidden = False
        await set_tool_enabled(plugin_id, tool_name, True)
        await set_tool_hidden(plugin_id, tool_name, False)
        tool_visibility.show(tool_name)
        tool_visibility.show_gateway(tool_name)
        # Unified gateway still owns MCP list_tools — re-hide non-allowlisted tools.
        try:
            from db_layer.gateway_settings_store import get_gateway_unified
            from core.proxy_tools.tool_visibility import GATEWAY_ALWAYS_VISIBLE, proxy_exposed_name
            exposed = proxy_exposed_name(plugin_id, tool_name)
            if await get_gateway_unified() and exposed not in GATEWAY_ALWAYS_VISIBLE:
                tool_visibility.hide_gateway(exposed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "tool state ENABLED: gateway re-hide failed for %s.%s",
                plugin_id, tool_name,
                exc_info=True,
            )
    elif state == ToolState.HIDDEN:
        is_enabled = True
        is_hidden = True
        await set_tool_enabled(plugin_id, tool_name, True)
        await set_tool_hidden(plugin_id, tool_name, True)
        tool_visibility.show(tool_name)
        tool_visibility.hide_gateway(tool_name)
    elif state == ToolState.DISABLED:
        is_enabled = False
        is_hidden = False
        await set_tool_enabled(plugin_id, tool_name, False)
        await set_tool_hidden(plugin_id, tool_name, False)
        tool_visibility.hide(tool_name)

    return is_enabled, is_hidden


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools/{tool_name}/state",
    methods=["POST"],
    name="api_set_tool_state",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.tool",
)
async def set_tool_state(request: Request) -> Response:
    """Sets a tool's state unified: enabled, hidden, or disabled."""
    plugin_id = canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    tool_name = request.path_params.get("tool_name", "").strip()
    if not plugin_id or not tool_name:
        return JSONResponse(
            {"error": "missing plugin_id or tool_name"}, status_code=400
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict) or "state" not in body:
        return JSONResponse({"error": "missing state field in body"}, status_code=400)

    state_val = body["state"]
    try:
        state = ToolState(state_val)
    except ValueError:
        return JSONResponse({"error": f"invalid state: {state_val}"}, status_code=400)

    logger.info(
        "Setting tool %s.%s state to %s", plugin_id, tool_name, state.value
    )

    try:
        is_enabled, is_hidden = await _apply_tool_state(plugin_id, tool_name, state)

        try:
            from core.plugin_loader.plugin_registry import get_registry
            await get_registry().lifecycle.reinitialize_plugin(plugin_id)
        except Exception as reinit_exc:
            logger.warning(
                "tool state reinitialize %s.%s failed (non-fatal): %s",
                plugin_id,
                tool_name,
                reinit_exc,
            )

        try:
            await refresh_tools_summary()
        except Exception as exc:
            logger.warning(
                "refresh after tool-state-toggle %s.%s: %s",
                plugin_id,
                tool_name,
                exc,
            )

        return JSONResponse({
            "plugin_id": plugin_id,
            "tool_name": tool_name,
            "state": state.value,
            "is_enabled": is_enabled,
            "is_hidden": is_hidden,
        })
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"tool state set {plugin_id}.{tool_name} -> {state.value}")


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools/batch-state",
    methods=["POST"],
    name="api_set_tools_batch_state",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.tool",
)
async def set_tools_batch_state(request: Request) -> Response:
    """Sets state and/or permission for a batch of tools in a plugin."""
    plugin_id = canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    if not plugin_id:
        return JSONResponse({"error": "missing plugin_id"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    tool_names = body.get("tool_names")
    if not isinstance(tool_names, list) or not tool_names:
        return JSONResponse({"error": "tool_names must be a non-empty list"}, status_code=400)

    state_val = body.get("state")
    permission_body = body.get("permission")

    if state_val is None and permission_body is None:
        return JSONResponse({"error": "either state or permission must be provided"}, status_code=400)

    state = None
    if state_val is not None:
        try:
            state = ToolState(state_val)
        except ValueError:
            return JSONResponse({"error": f"invalid state: {state_val}"}, status_code=400)

    kwargs = {}
    if permission_body is not None:
        if not isinstance(permission_body, dict):
            return JSONResponse({"error": "permission must be an object"}, status_code=400)
        # Validate that optional fields are of the correct type using the enum map
        for field_enum, expected_type in PERMISSION_FIELD_TYPES.items():
            field_name = field_enum.value
            if field_name in permission_body and permission_body[field_name] is not None:
                if not isinstance(permission_body[field_name], expected_type):
                    type_name = expected_type.__name__
                    return JSONResponse({"error": f"{field_name} must be a {type_name}"}, status_code=400)
        # Collect validated fields dynamically from request body
        kwargs = {
            field_enum.value: permission_body[field_enum.value]
            for field_enum in PERMISSION_FIELD_TYPES
            if field_enum.value in permission_body
        }

    logger.info(
        "Batch setting %d tools state/permission in plugin %s",
        len(tool_names),
        plugin_id,
    )

    try:
        updated = []
        # Sequential on purpose: _apply_tool_state/set_permission mutate shared
        # plugin registry state; asyncio.gather would race. Correctness > batch latency.
        for tool_name in tool_names:
            tool_name = tool_name.strip()
            item = {"tool_name": tool_name}
            if state is not None:
                is_enabled, is_hidden = await _apply_tool_state(plugin_id, tool_name, state)
                item["state"] = state.value
                item["is_enabled"] = is_enabled
                item["is_hidden"] = is_hidden
            if permission_body is not None:
                pol = await set_permission(plugin_id, tool_name, **kwargs)
                item["permission"] = pol
            updated.append(item)

        try:
            from core.plugin_loader.plugin_registry import get_registry
            await get_registry().lifecycle.reinitialize_plugin(plugin_id)
        except Exception as reinit_exc:
            logger.warning(
                "batch tool state reinitialize %s failed (non-fatal): %s",
                plugin_id,
                reinit_exc,
            )

        try:
            await refresh_tools_summary()
        except Exception as exc:
            logger.warning(
                "refresh after batch tool-state-toggle %s: %s",
                plugin_id,
                exc,
            )

        return JSONResponse({
            "plugin_id": plugin_id,
            "updated": updated,
            "count": len(updated),
        })
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"batch tool state set failed for plugin {plugin_id}")


async def _set_tool(request: Request, enabled: bool) -> Response:
    """Shared handler: persist state, then hide/show the tool live."""
    plugin_id = canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    tool_name = request.path_params.get("tool_name", "").strip()
    if not plugin_id or not tool_name:
        return JSONResponse(
            {"error": "missing plugin_id or tool_name"}, status_code=400
        )

    logger.info(
        "%s tool %s.%s", "Enabling" if enabled else "Disabling", plugin_id, tool_name
    )
    try:
        await set_tool_enabled(plugin_id, tool_name, enabled)
        # Live apply against FastMCP. DB is source of truth; a missing snapshot
        # entry (tool added post-startup) just means the transform is a no-op.
        tool_visibility.show(tool_name) if enabled else tool_visibility.hide(tool_name)

        # Robust hot-swap: reinitialize the plugin so on_load re-filters
        # disabled tools and (re)contributes only kept routes to the registry.
        # This ensures a tool disabled at startup becomes available (or
        # unavailable) to run_graph / discover_tools immediately.
        try:
            from core.plugin_loader.plugin_registry import get_registry
            await get_registry().lifecycle.reinitialize_plugin(plugin_id)
        except Exception as reinit_exc:
            logger.warning("tool toggle reinitialize %s.%s failed (non-fatal): %s", plugin_id, tool_name, reinit_exc)

        try:
            await refresh_tools_summary()
        except Exception as exc:
            logger.warning("refresh after tool-enabled-toggle %s.%s: %s", plugin_id, tool_name, exc)

        return JSONResponse(
            {"plugin_id": plugin_id, "tool_name": tool_name, "enabled": enabled}
        )
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"tool toggle {plugin_id}.{tool_name} -> {enabled}")


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools/{tool_name}/enable",
    methods=["POST"],
    name="api_enable_tool",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.tool",
)
async def enable_tool(request: Request) -> Response:
    """Expose a single tool to MCP clients (tool_config.is_enabled = TRUE)."""
    return await _set_tool(request, enabled=True)


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools/{tool_name}/enable",
    methods=["DELETE"],
    name="api_disable_tool",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.tool",
)
async def disable_tool(request: Request) -> Response:
    """Hide a single tool from MCP clients (tool_config.is_enabled = FALSE)."""
    return await _set_tool(request, enabled=False)


async def _set_tool_hidden(request: Request, hidden: bool) -> Response:
    """Shared handler: persist gateway hidden state, then hide/show the tool live."""
    plugin_id = canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    tool_name = request.path_params.get("tool_name", "").strip()
    if not plugin_id or not tool_name:
        return JSONResponse({"error": "missing plugin_id or tool_name"}, status_code=400)

    logger.info("%s tool %s.%s (gateway)", "Hiding" if hidden else "Showing", plugin_id, tool_name)
    try:
        await set_tool_hidden(plugin_id, tool_name, hidden)
        # Live apply. Allowlisted tools (run_graph, auth) are skipped by hide_gateway.
        applied = tool_visibility.hide_gateway(tool_name) if hidden else tool_visibility.show_gateway(tool_name)
        # Unified mode still owns MCP list_tools — keep non-allowlisted tools hidden.
        if not hidden:
            try:
                from db_layer.gateway_settings_store import get_gateway_unified
                from core.proxy_tools.tool_visibility import GATEWAY_ALWAYS_VISIBLE, proxy_exposed_name
                exposed = proxy_exposed_name(plugin_id, tool_name)
                if await get_gateway_unified() and exposed not in GATEWAY_ALWAYS_VISIBLE:
                    tool_visibility.hide_gateway(exposed)
                    applied = True
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "tool unhide: gateway re-hide failed for %s.%s",
                    plugin_id, tool_name,
                    exc_info=True,
                )
        # Refresh summary for gateway visibility change
        try:
            await refresh_tools_summary()
        except Exception as exc:
            logger.warning("refresh after hide-toggle %s.%s: %s", plugin_id, tool_name, exc)
        return JSONResponse(
            {"plugin_id": plugin_id, "tool_name": tool_name, "hidden": hidden, "applied": applied}
        )
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"tool hide-toggle {plugin_id}.{tool_name} -> {hidden}")


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools/{tool_name}/hide",
    methods=["POST"],
    name="api_hide_tool",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.tool",
)
async def hide_tool(request: Request) -> Response:
    """Gateway-hide a single tool (tool_config.is_hidden = TRUE). Still run-able by run_graph."""
    return await _set_tool_hidden(request, hidden=True)


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools/{tool_name}/hide",
    methods=["DELETE"],
    name="api_unhide_tool",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.tool",
)
async def unhide_tool(request: Request) -> Response:
    """Restore a gateway-hidden tool to MCP exposure (tool_config.is_hidden = FALSE)."""
    return await _set_tool_hidden(request, hidden=False)


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools/{tool_name}/embedding-model",
    methods=["POST"],
    name="api_set_tool_embedding_model",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.tool",
)
async def set_tool_embedding_model_route(request: Request) -> Response:
    """Set the embedding model configured for a tool."""
    plugin_id = canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    tool_name = request.path_params.get("tool_name", "").strip()
    if not plugin_id or not tool_name:
        return JSONResponse(
            {"error": "missing plugin_id or tool_name"}, status_code=400
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json", "message": "Expected a JSON object"}, status_code=400)

    model_id = body.get("model")
    if model_id is not None:
        model_id = str(model_id).strip()
        if not model_id or model_id.lower() == "null":
            model_id = None

    try:
        await set_tool_embedding_model(plugin_id, tool_name, model_id)
        return JSONResponse(
            {"plugin_id": plugin_id, "tool_name": tool_name, "embedding_model": model_id}
        )
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"tool embedding-model set {plugin_id}.{tool_name} -> {model_id}")


# ---------------------------------------------------------------------------
# Per-tool permission policy (read/write/confirm) — Part B
# ---------------------------------------------------------------------------

class PermissionField(str, Enum):
    """Supported fields in the tool permission schema."""
    ALLOW_READ = "allow_read"
    ALLOW_WRITE = "allow_write"
    REQUIRE_CONFIRMATION = "require_confirmation"


# Map each permission enum field to its expected validator type
PERMISSION_FIELD_TYPES = {
    PermissionField.ALLOW_READ: bool,
    PermissionField.ALLOW_WRITE: bool,
    PermissionField.REQUIRE_CONFIRMATION: bool,
}


async def _get_tool_permission(request: Request) -> Response:
    plugin_id = canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    tool_name = request.path_params.get("tool_name", "").strip()
    if not plugin_id or not tool_name:
        return JSONResponse({"error": "missing plugin_id or tool_name"}, status_code=400)
    try:
        pol = await get_permission(plugin_id, tool_name)
        return JSONResponse({"plugin_id": plugin_id, "tool_name": tool_name, "permission": pol})
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"get permission {plugin_id}.{tool_name}")


async def _set_tool_permission(request: Request) -> Response:
    plugin_id = canonical_plugin_id(request.path_params.get("plugin_id", "").strip())
    tool_name = request.path_params.get("tool_name", "").strip()
    if not plugin_id or not tool_name:
        return JSONResponse({"error": "missing plugin_id or tool_name"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    # Validate that optional fields are of the correct type using the enum map
    for field_enum, expected_type in PERMISSION_FIELD_TYPES.items():
        field_name = field_enum.value
        if field_name in body and body[field_name] is not None:
            if not isinstance(body[field_name], expected_type):
                type_name = expected_type.__name__
                return JSONResponse({"error": f"{field_name} must be a {type_name}"}, status_code=400)

    # Collect validated fields dynamically from request body
    kwargs = {
        field_enum.value: body[field_enum.value]
        for field_enum in PERMISSION_FIELD_TYPES
        if field_enum.value in body
    }

    try:
        pol = await set_permission(
            plugin_id,
            tool_name,
            **kwargs,
        )
        return JSONResponse({"plugin_id": plugin_id, "tool_name": tool_name, "permission": pol})
    except Exception as exc:
        return safe_error_response(exc, log_ctx=f"set permission {plugin_id}.{tool_name}")


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools/{tool_name}/permission",
    methods=["GET"],
    name="api_get_tool_permission",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.tool",
)
async def get_tool_permission(request: Request) -> Response:
    """Gets the security permissions for a specific tool (read/write/confirm)."""
    return await _get_tool_permission(request)


@http_route_registry.route(
    route="plugins",
    endpoint="{plugin_id}/tools/{tool_name}/permission",
    methods=["POST"],
    name="api_set_tool_permission",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.tool",
)
async def set_tool_permission(request: Request) -> Response:
    """Updates the security permissions for a specific tool (read/write/confirm)."""
    return await _set_tool_permission(request)
