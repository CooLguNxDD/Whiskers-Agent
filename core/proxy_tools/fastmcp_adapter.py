"""
fastmcp_adapter — single surface for FastMCP version-coupled access.

Isolates two kinds of FastMCP coupling behind one module so a future FastMCP upgrade only
requires changes here:
  1. Tool enumeration across FastMCP 3.x (``_local_provider._components``) and legacy 2.x
     (``_tool_manager``) internals, plus duck-typing accessors for tool metadata.
  2. The visibility-transform API (``server.disable()`` / ``server.enable()``).

Moved out of core/proxy_tools/static_tool_loader.py and core/proxy_tools/tool_visibility.py
(Phase 2 modularity refactor) verbatim — no logic changes.
"""

from __future__ import annotations

from typing import Any, Iterable
import logging
logger = logging.getLogger("whiskers")


def iter_tools(mcp_app: Any) -> list[Any]:
    """Return every *local/static* FastMCP tool (not mounted providers).

    FastMCP 3.x unified storage: ``_local_provider._components`` is a mixed dict
    keyed by ``"<type>:<name>"`` (e.g. ``"tool:create_record"``). Filter to
    ``"tool:"`` prefix to get only FunctionTool objects.

    **Does not include proxy tools** mounted via ``add_provider(..., namespace=)``.
    Use :func:`list_all_tool_names` for the full MCP-visible name set.
    """
    # FastMCP 3.x path
    local_provider = getattr(mcp_app, "_local_provider", None)
    if local_provider is not None:
        comps = getattr(local_provider, "_components", None)
        if isinstance(comps, dict) and comps:
            return [v for k, v in comps.items() if k.startswith("tool:")]

    # Legacy FastMCP 2.x path — _tool_manager with _tools / list_tools / tools
    mgr = getattr(mcp_app, "_tool_manager", None)
    if mgr is None:
        return []
    for attr in ("list_tools", "_tools", "tools"):
        target = getattr(mgr, attr, None)
        if target is None:
            continue
        try:
            result = target() if callable(target) else target
        except Exception:
            logger.debug("fastmcp_adapter.py: continue after exception", exc_info=True)
            continue
        if isinstance(result, dict):
            return list(result.values())
        if isinstance(result, (list, tuple, set)):
            return list(result)
    return []


async def list_all_tool_names(mcp_app: Any) -> set[str]:
    """Return every MCP-exposed tool name, including namespaced proxy providers.

    Walks ``mcp_app.providers`` and calls each provider's ``list_tools()``
    (provider-level: includes Namespace transforms, still includes disabled
    tools — server Visibility filtering is *not* applied). Falls back to
    :func:`iter_tools` for static local components when providers are empty.
    """
    names: set[str] = set()
    if mcp_app is None:
        return names

    providers = getattr(mcp_app, "providers", None)
    if isinstance(providers, list) and providers:
        for provider in providers:
            try:
                tools = await provider.list_tools()
            except Exception:
                logger.warning("fastmcp_adapter.py: continue after exception", exc_info=True)
                continue
            for tool in tools or []:
                n = tool_name(tool)
                if n:
                    names.add(n)

    # Always union local static tools (belt-and-suspenders if provider walk misses).
    for tool in iter_tools(mcp_app):
        n = tool_name(tool)
        if n:
            names.add(n)
    return names


def tool_description(tool: Any, fn: Any = None) -> str:
    """Extract a human-readable description from a FastMCP tool object."""
    for attr in ("description", "title"):
        value = getattr(tool, attr, None)
        if value:
            return str(value)
    if fn and fn.__doc__:
        return fn.__doc__.strip().split("\n")[0]
    value = getattr(tool, "name", None)
    if value:
        return str(value)
    return ""


def tool_input_schema(tool: Any) -> dict:
    """Pull the JSON-schema parameters off a FastMCP tool object."""
    for attr in ("parameters", "input_schema", "inputSchema", "schema"):
        value = getattr(tool, attr, None)
        if value:
            if isinstance(value, dict):
                return value
            # Pydantic model / FieldInfo — try .model_json_schema()
            mj = getattr(value, "model_json_schema", None)
            if callable(mj):
                try:
                    return mj()
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "fastmcp_adapter: schema generation failed for tool %s: %s",
                        getattr(tool, "name", "?"), exc,
                    )
    return {}


def tool_tags(tool: Any) -> tuple[str, ...]:
    """Retrieve the optional tags assigned to a FastMCP tool object."""
    raw = getattr(tool, "tags", None)
    if not raw:
        return ()
    if isinstance(raw, (set, frozenset, list, tuple)):
        return tuple(str(t) for t in raw)
    return (str(raw),)


def tool_callable(tool: Any) -> Any:
    """Resolve the underlying executable function for a FastMCP tool object."""
    for attr in ("fn", "func", "callable", "handler", "_fn"):
        value = getattr(tool, attr, None)
        if value is not None and callable(value):
            return value
    return None


def tool_name(tool: Any) -> str:
    """Determine the exposed operational name for a FastMCP tool object."""
    return str(getattr(tool, "name", "") or getattr(tool, "operation_id", ""))


_TOOL_COMPONENT = {"tool"}


def disable_tool(mcp_app: Any, names: Iterable[str], components: Iterable[str] | None = None) -> None:
    """Apply a FastMCP visibility-disable transform for ``names``."""
    mcp_app.disable(names=set(names), components=components or _TOOL_COMPONENT)


def enable_tool(mcp_app: Any, names: Iterable[str], components: Iterable[str] | None = None) -> None:
    """Apply a FastMCP visibility-enable transform for ``names``."""
    mcp_app.enable(names=set(names), components=components or _TOOL_COMPONENT)
