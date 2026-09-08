"""core/context/tool_meta — local-only tool metadata resolution for MCP middleware.

``ScopeEnforcementMiddleware`` and ``ResponseShapeMiddleware`` both need to map a
called tool ``name`` to its ``plugin_id`` / ``tags`` / whether it declares its own
``_response_shape`` param. The naive way to get that is ``FastMCP.get_tool(name)``,
but on a composite server that fans out to *every* mounted provider
(``AggregateProvider._get_tool`` gathers ``provider.get_tool(name)`` across all of
them) and — worse — falls back to ``list_tools()`` whenever the resolved tool is
disabled/hidden (exactly what gateway-unified mode does), which for a
``ProxyProvider`` always refetches the upstream server, never reads its own cache.
Net effect: calling one local tool re-lists every mounted proxy (GitHub, Notion, …)
2-3x per call.

This module resolves the same metadata without ever touching a provider:
  1. a per-request memo (kills in-request duplication across the two middlewares)
  2. ``route_registry`` (existing fast path for statically/dynamically routed tools)
  3. the local static tool index (``fastmcp_adapter.iter_tools`` — explicitly
     excludes mounted proxy providers)
  4. a proxy-namespace prefix match against ``mcp.providers`` transforms (no
     network call — only reads the already-mounted ``Namespace`` transform's prefix)
  5. miss → empty ``ToolMeta``, same fail-open behavior the old ``except`` branches had

Proxy tools are only ever contacted when actually invoked (``call_tool``), never
for metadata resolution.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("whiskers")

_SHAPE_ARG_KEY = "_response_shape"

_INDEX_TTL_S = 5.0


@dataclass(frozen=True)
class ToolMeta:
    """Resolved metadata for one tool name — never triggers a network call."""

    plugin_id: str
    tags: tuple[str, ...]
    declares_shape: bool


_EMPTY = ToolMeta("", (), False)

# Per-request memo — set by whichever middleware resolves first, read by the other.
_request_memo: ContextVar[dict[str, ToolMeta] | None] = ContextVar("_tool_meta_memo", default=None)

# Local static tool index cache: name -> ToolMeta. Invalidated by version bump
# (plugin load/reinit/teardown, proxy mount/unmount) or TTL expiry as a backstop.
_index: dict[str, ToolMeta] | None = None
_index_version: int = -1
_index_built_at: float = 0.0
_version: int = 0


def bump_version() -> None:
    """Invalidate the local tool index. Call after any plugin/proxy load change."""
    global _version
    _version += 1


def _build_local_index(mcp_app: Any) -> dict[str, ToolMeta]:
    from core.proxy_tools.fastmcp_adapter import iter_tools, tool_name, tool_tags, tool_input_schema

    try:
        from core.plugin_loader.plugin_registry import get_registry
        loaded_plugin_ids = set(get_registry().lifecycle._plugin_id_map.keys())
    except Exception as exc:
        logger.error("tool_meta: plugin registry lookup failed: %s", exc)
        loaded_plugin_ids = set()

    out: dict[str, ToolMeta] = {}
    for tool in iter_tools(mcp_app):
        name = tool_name(tool)
        if not name:
            continue
        tags = tool_tags(tool)
        schema = tool_input_schema(tool)
        declares_shape = _SHAPE_ARG_KEY in (schema.get("properties") or {})
        # Tools registered directly via mcp.tool(...) (e.g. jules_plugin's
        # dynamic-tools loader) are absent from route_registry — cross-reference
        # tags against loaded plugin ids to identify which tag is the plugin_id
        # (mirrors the pre-refactor app.get_tool() fallback behavior).
        plugin_id = next((t for t in tags if t in loaded_plugin_ids), "")
        out[name] = ToolMeta(plugin_id=plugin_id, tags=tags, declares_shape=declares_shape)
    return out


def _get_local_index(mcp_app: Any) -> dict[str, ToolMeta]:
    global _index, _index_version, _index_built_at
    now = time.monotonic()
    if (
        _index is not None
        and _index_version == _version
        and (now - _index_built_at) < _INDEX_TTL_S
    ):
        return _index
    try:
        _index = _build_local_index(mcp_app)
    except Exception:
        logger.debug("tool_meta: local index build failed", exc_info=True)
        _index = {}
    _index_version = _version
    _index_built_at = now
    return _index


def _proxy_plugin_id_for(mcp_app: Any, name: str) -> str:
    """Match ``name`` against mounted proxy namespaces without contacting any provider."""
    providers = getattr(mcp_app, "providers", None)
    if not isinstance(providers, list):
        return ""
    for entry in providers:
        for transform in getattr(entry, "transforms", ()) or ():
            prefix = getattr(transform, "_prefix", None)
            if prefix and name.startswith(f"{prefix}_"):
                return f"proxy_{prefix}"
    return ""


async def resolve_tool_meta(context: Any, name: str) -> ToolMeta:
    """Resolve plugin_id/tags/declares_shape for ``name`` — local lookups only."""
    memo = _request_memo.get()
    if memo is None:
        memo = {}
        _request_memo.set(memo)
    cached = memo.get(name)
    if cached is not None:
        return cached

    meta = await _resolve_uncached(context, name)
    memo[name] = meta
    return meta


async def _resolve_uncached(context: Any, name: str) -> ToolMeta:
    from core.context._registries import route_registry

    plugin_id = ""
    tags: tuple[str, ...] = ()
    try:
        desc = route_registry.get(name) if route_registry else None
        if desc is not None:
            plugin_id, tags = desc.plugin_id or "", tuple(desc.tags or ())
    except Exception as exc:
        logger.error("tool_meta: route_registry lookup failed for %r: %s", name, exc)

    mcp_app = None
    try:
        fastmcp_ctx = getattr(context, "fastmcp_context", None)
        mcp_app = fastmcp_ctx.fastmcp if fastmcp_ctx else None
    except Exception as exc:
        logger.warning("_resolve_uncached: failed to extract fastmcp from context: %s", exc)
        mcp_app = None

    if mcp_app is None:
        return ToolMeta(plugin_id, tags, False) if (plugin_id or tags) else _EMPTY

    local = _get_local_index(mcp_app).get(name)
    if local is not None:
        return ToolMeta(
            plugin_id=plugin_id or local.plugin_id,
            tags=tags or local.tags,
            declares_shape=local.declares_shape,
        )

    if not plugin_id:
        plugin_id = _proxy_plugin_id_for(mcp_app, name)

    if not plugin_id and not tags:
        return _EMPTY
    return ToolMeta(plugin_id, tags, False)
