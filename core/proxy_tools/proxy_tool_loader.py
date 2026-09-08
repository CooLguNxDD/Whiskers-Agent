"""
proxy_tool_loader — collect RouteDescriptors from upstream proxy tools.

Creates RouteDescriptor objects mapping proxy tools to the dynamic route registry.
Exposes a fast-path callable_ref wrapping execution via the proxy provider's server.
"""
from __future__ import annotations
import json
import logging
from typing import Any, Tuple

from core.route_registry.route_descriptor import RouteDescriptor
from core.proxy_tools.static_tool_loader import _qualify, _tool_input_schema

logger = logging.getLogger("whiskers")


async def _shape_proxy_result(
    parsed: Any, raw_op_id: str, request_params: dict[str, Any] | None
) -> Any:
    """Run a parsed proxy response through the shared sanitize+CSV pipeline.

    Keyed by the unqualified op id so config/tools_api_config.json entries
    (e.g. ListSessions / GetSession) apply. Uses async shape so MinIO
    offload_minio can run before strip. Errors and shaping failures pass
    through raw so a proxy call never breaks over shaping.
    """
    try:
        from utils.config_registry import get_config_registry
        if not get_config_registry().server.get("graph", {}).get("proxy_response_shaping", True):
            return parsed
    except Exception:
        logger.debug("proxy_response_shaping config unavailable; shaping with defaults", exc_info=True)
    try:
        from utils.api_utils import apply_response_shape
        return await apply_response_shape(
            parsed,
            operation_id=raw_op_id,
            tool_name=raw_op_id,
            request_params=request_params,
            shape=None,  # merge base + op + endpoint → full pipeline (offload then strip/CSV)
        )
    except Exception:
        logger.debug("Proxy response shaping failed for %s; returning raw", raw_op_id, exc_info=True)
        return parsed


def _raise_proxy_tool_error(tool_name: str, message: str, *, http_status: int | None = None) -> None:
    """Raise a fastmcp ToolError carrying the same http_status/api_error_type stamp
    ``utils.api_utils._handle_api_exception`` attaches, so a proxy failure is reported
    as a real MCP tool error (not swallowed into a 200-OK ``{"status": "error"}`` body)
    while callers that inspect ``is_recoverable()`` still see a usable status.
    """
    from fastmcp.exceptions import ToolError
    from utils.api_utils import _attach_error_meta

    error = {"http_status": http_status, "error": "proxy_tool_failed", "message": message}
    raise _attach_error_meta(ToolError(f"Proxy tool {tool_name} failed: {message}"), error)


def make_proxy_callable(provider: Any, tool_name: str) -> Any:
    """Create a thin wrapper that invokes the tool on the proxy provider's server."""
    async def call_proxy_tool(**kwargs):
        """Asynchronously call the proxy tool with the given keyword arguments."""
        logger.info("Proxy invocation: calling tool '%s' on provider server with args: %s", tool_name, kwargs)
        from utils.response_shape import offload_raw_result
        try:
            # Call the tool on the provider's server (FastMCP instance)
            result = await provider.server.call_tool(tool_name, kwargs)

            # Extract and format content safely
            if hasattr(result, "content") and isinstance(result.content, list):
                # If the underlying MCP tool returned an error, capture its text message
                if getattr(result, "isError", False):
                    err_texts = [getattr(item, "text", "") for item in result.content]
                    _raise_proxy_tool_error(tool_name, " ".join(err_texts).strip())

                if len(result.content) == 1 and hasattr(result.content[0], "text"):
                    text = result.content[0].text
                    try:
                        parsed = json.loads(text)
                    except (json.JSONDecodeError, ValueError):
                        # Non-JSON text (e.g. a raw file/CSV body) skips shape/CSV
                        # by design but still deserves MinIO offload if huge.
                        return {
                            "status": "ok",
                            "data": await offload_raw_result(
                                text, tool_name=tool_name, request_params=kwargs
                            ),
                        }
                    return await _shape_proxy_result(parsed, tool_name, kwargs)

                serialized = []
                for item in result.content:
                    if hasattr(item, "text"):
                        serialized.append({
                            "type": "text",
                            "text": await offload_raw_result(
                                item.text, tool_name=tool_name, request_params=kwargs
                            ),
                        })
                    elif hasattr(item, "data"):
                        serialized.append({
                            "type": "image",
                            "data": item.data,
                            "mimeType": getattr(item, "mimeType", "")
                        })
                return serialized
            return result
        except Exception as exc:
            from fastmcp.exceptions import ToolError
            if isinstance(exc, ToolError):
                raise
            logger.exception("Proxy invocation failed for tool '%s': %s", tool_name, exc)
            _raise_proxy_tool_error(tool_name, str(exc))

    # Marker so executor prune guards (and any defensive arg merges) can
    # reliably identify dynamic proxy forwarders (plugin_id may vary in case
    # or the selected/route may be a fallback).
    call_proxy_tool._is_proxy_wrapper = True
    return call_proxy_tool


def _max_custom_desc_chars() -> int:
    """Read the max custom description character limit from config or fall back to 2000."""
    try:
        from utils.config_registry import get_config_registry
        val = get_config_registry().server.get("graph", {}).get("max_proxy_custom_description_chars", 2000)
        return int(val)
    except Exception:
        return 2000


def collect_from_proxy(
    name: str,
    tools: list[Any],
    provider: Any,
    custom_description: str | None = None,
    workspace_label: str | None = None,
) -> list[RouteDescriptor]:
    """Build RouteDescriptors from discovered proxy tools.
    
    If custom_description is provided, it is prepended to the description of each route descriptor.
    """
    plugin_id = f"proxy_{name}"
    descriptors: list[RouteDescriptor] = []
    seen = set()

    for tool in tools:
        raw_op_id = getattr(tool, "name", "")
        if not raw_op_id:
            continue
        op_id = _qualify(plugin_id, raw_op_id)
        if op_id in seen:
            continue
        seen.add(op_id)
        
        fn = make_proxy_callable(provider, raw_op_id)
        
        desc = getattr(tool, "description", "") or raw_op_id
        if custom_description:
            cap = _max_custom_desc_chars()
            trimmed = custom_description.strip()[:cap]
            desc = f"{trimmed}\n\n{desc.strip()}".strip()
        schema = _tool_input_schema(tool)
            
        tags: tuple[str, ...] = (plugin_id, "proxy")
        if workspace_label:
            tags = tags + (f"workspace:{workspace_label}",)
        descriptors.append(RouteDescriptor(
            plugin_id=plugin_id,
            operation_id=op_id,
            description=desc,
            parameters=schema,
            method="CALL",
            path_template=op_id,
            is_fast_path=True,
            callable_ref=fn,
            tags=tags,
            workspace_label=workspace_label,
        ))

    return descriptors
