"""
static_tool_loader — collect RouteDescriptors from ``@mcp.tool``-decorated
Python functions.

The plugin caller passes ``plugin_id`` and a list of dotted module paths whose
import causes ``@mcp.tool()`` side effects to fire. The loader then queries
FastMCP's tool manager and emits one RouteDescriptor per tool tagged with
``plugin_id`` (the convention already used across the codebase — e.g.
``@mcp.tool(tags={"search_plugin", "write"})``).

Each resulting descriptor is fast-path (``is_fast_path=True``,
``callable_ref=<the function>``) so the executor can invoke the tool directly
without going through the HTTP builder.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from typing import Any, Iterable

from core.route_registry.route_descriptor import RouteDescriptor

logger = logging.getLogger("whiskers")


def _import_modules(module_paths: Iterable[str]) -> None:
    """Import each dotted module path; failures are logged, not raised.

    Tool registration is a side effect of import, so this is what actually
    populates ``mcp._tool_manager``.
    """
    for path in module_paths:
        try:
            importlib.import_module(path)
        except Exception as exc:
            logger.warning(
                "static_tool_loader: failed to import '%s' for plugin tools: %s",
                path, exc,
            )


from core.proxy_tools.fastmcp_adapter import (
    iter_tools as _iter_fastmcp_tools,
    tool_description as _tool_description,
    tool_input_schema as _tool_input_schema,
    tool_tags as _tool_tags,
    tool_callable as _tool_callable,
    tool_name as _tool_name,
)


def _qualify(plugin_id: str, op_id: str) -> str:
    """Prefix ``op_id`` with ``{plugin_id}__`` unless already qualified.

    Cross-plugin collision protection — the registry key is
    ``(plugin_id, operation_id)``, but the planner / embedding search returns
    ``operation_id`` standalone, so two plugins shipping the same short tool
    name would still be ambiguous downstream. Qualifying at registration time
    keeps the public route identifier globally unique.
    """
    prefix = f"{plugin_id}__"
    if op_id.startswith(prefix):
        return op_id
    return f"{prefix}{op_id}"


def collect_from(
    plugin_id: str,
    module_paths: Iterable[str],
    *,
    tag: str | None = None,
    mcp_app=None,
) -> list[RouteDescriptor]:
    """Import modules, then snapshot FastMCP tools tagged with ``plugin_id``.

    Parameters
    ----------
    plugin_id
        Identifier the registry keys routes by. Also the default tag filter.
    module_paths
        Dotted module paths whose import triggers ``@mcp.tool()`` registration.
    tag
        Override the tag used to filter tools. Defaults to ``plugin_id``.
    mcp_app
        FastMCP app instance. When omitted, imports lazily from
        ``core.context``.
    """
    _import_modules(module_paths)

    if mcp_app is None:
        from core.context import mcp as _mcp
        mcp_app = _mcp

    filter_tag = tag or plugin_id
    descriptors: list[RouteDescriptor] = []
    seen: set[str] = set()

    for tool in _iter_fastmcp_tools(mcp_app):
        tags = _tool_tags(tool)
        if filter_tag and filter_tag not in tags:
            continue
        raw_op_id = _tool_name(tool)
        if not raw_op_id:
            continue
        op_id = _qualify(plugin_id, raw_op_id)
        if op_id in seen:
            continue
        seen.add(op_id)
        fn = _tool_callable(tool)
        tags_t = tuple(tags) if not isinstance(tags, tuple) else tags
        tags_lower = {str(t).lower() for t in tags_t}
        if "admin" in tags_lower:
            access = "admin"
        elif "write" in tags_lower:
            access = "write"
        else:
            access = "read"
        descriptors.append(RouteDescriptor(
            plugin_id=plugin_id,
            operation_id=op_id,
            description=_tool_description(tool, fn),
            parameters=_tool_input_schema(tool),
            method="CALL",
            path_template=op_id,
            is_fast_path=fn is not None,
            callable_ref=fn,
            tags=tags_t,
            access=access,
        ))

    if not descriptors:
        logger.warning(
            "static_tool_loader: collected 0 routes for plugin '%s' "
            "(tag filter=%r). Verify the plugin tags its tools with this id.",
            plugin_id, filter_tag,
        )
    return descriptors
