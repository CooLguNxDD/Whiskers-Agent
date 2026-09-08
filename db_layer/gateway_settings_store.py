"""
gateway_settings_store — CRUD for live gateway config overrides.

``server_settings['gateway_unified']`` holds the persisted bool (default from
utils.server_config.GATEWAY_UNIFIED when absent or DB unavailable). This makes
GATEWAY_UNIFIED live-togglable without restart; the JSON value is the fallback.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db_layer.connection import get_async_session
from db_layer.models import ServerSetting
from utils.server_config import GATEWAY_UNIFIED as JSON_DEFAULT

logger = logging.getLogger("whiskers")

_GATEWAY_KEY = "gateway_unified"


def _db_available() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


async def get_gateway_unified() -> bool:
    """Return the live gateway unified flag, falling back to JSON default."""
    if not _db_available():
        return JSON_DEFAULT
    async with get_async_session() as session:
        row = (await session.execute(
            select(ServerSetting.value).where(ServerSetting.key == _GATEWAY_KEY)
        )).fetchone()
    if row and row[0] is not None:
        val = row[0]
        if isinstance(val, dict):
            # support legacy {"enabled": bool} or direct bool
            if "enabled" in val:
                return bool(val["enabled"])
            if "run_graph_unified" in val:
                return bool(val["run_graph_unified"])
        if isinstance(val, bool):
            return val
        # string/num coercion
        if isinstance(val, str):
            return val.lower() in ("1", "true", "yes", "on")
        return bool(val)
    return JSON_DEFAULT


async def set_gateway_unified(enabled: bool) -> bool:
    """Persist the gateway unified flag (bool stored under server_settings)."""
    if not _db_available():
        logger.warning("set_gateway_unified: DB unavailable, change not persisted (JSON default remains)")
        return enabled
    value = {"enabled": bool(enabled)}
    stmt = (
        pg_insert(ServerSetting)
        .values(key=_GATEWAY_KEY, value=value)
        .on_conflict_do_update(index_elements=["key"], set_={"value": value})
    )
    async with get_async_session() as session:
        await session.execute(stmt)
        await session.commit()
    logger.info("gateway_settings: run_graph_unified=%s", enabled)
    return enabled


async def collect_all_mcp_tool_names() -> set[str]:
    """Return every tool name registered on the live FastMCP app.

    Includes local/static tools **and** namespaced proxy tools mounted via
    ``add_provider(..., namespace=)``. Source of truth for gateway unified hide
    (not DB flags). Returns empty when MCP is unbound or enumeration fails.
    """
    try:
        from core.context import mcp
        from core.proxy_tools.fastmcp_adapter import list_all_tool_names
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("collect_all_mcp_tool_names: import failed", exc_info=True)
        return set()
    if mcp is None:
        return set()
    try:
        names = await list_all_tool_names(mcp)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("collect_all_mcp_tool_names: list_all_tool_names failed", exc_info=True)
        names = set()

    # Route-registry fallback: proxy routes may be known even if a provider
    # list_tools call fails (upstream flaky). Exposed names = namespace_raw.
    try:
        names |= await _collect_proxy_route_exposed_names()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("collect_all_mcp_tool_names: proxy route fallback failed", exc_info=True)
    return names


async def _collect_proxy_route_exposed_names() -> set[str]:
    """Map proxy_* route descriptors to FastMCP-exposed ``namespace_tool`` names."""
    from core.proxy_tools.tool_visibility import proxy_exposed_name

    names: set[str] = set()
    try:
        from core.plugin_loader.plugin_registry import get_registry
        rr = get_registry().route_registry
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("_collect_proxy_route_exposed_names: registry unavailable", exc_info=True)
        return names

    routes_map = getattr(rr, "_routes", None)
    if not isinstance(routes_map, dict):
        # Fall back to per-plugin lookup if private map unavailable.
        try:
            from db_layer.models import PluginModel
            async with get_async_session() as session:
                rows = await session.execute(
                    select(PluginModel.id).where(
                        PluginModel.is_active.is_(True),
                        PluginModel.id.like("proxy_%"),
                    )
                )
                pids = [pid for (pid,) in rows.all()]
            if pids:
                results = await asyncio.gather(
                    *(collect_plugin_exposed_tool_names(pid) for pid in pids)
                )
                for part in results:
                    names |= part
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "_collect_proxy_route_exposed_names: DB proxy fallback failed",
                exc_info=True,
            )
        return names

    for (plugin_id, operation_id), _desc in routes_map.items():
        if not plugin_id or not str(plugin_id).startswith("proxy_"):
            continue
        if not operation_id:
            continue
        prefix = f"{plugin_id}__"
        raw = (
            operation_id[len(prefix):]
            if operation_id.startswith(prefix)
            else operation_id
        )
        exposed = proxy_exposed_name(plugin_id, raw)
        if exposed:
            names.add(exposed)
    return names


async def collect_gateway_unified_hide_names() -> set[str]:
    """Names to hide when run_graph_unified is ON: all MCP tools minus allowlist.

    Only ``GATEWAY_ALWAYS_VISIBLE`` (run_graph, discover_tools, auth) stay on
    list_tools / direct call_tool. Covers local plugins **and** namespaced
    proxy tools. Everything else remains reachable via run_graph's route
    registry / fast-path.
    """
    from core.proxy_tools.tool_visibility import GATEWAY_ALWAYS_VISIBLE

    return {
        n for n in await collect_all_mcp_tool_names()
        if n and n not in GATEWAY_ALWAYS_VISIBLE
    }


async def collect_selective_hidden_names() -> set[str]:
    """Tools flagged for selective hide (independent of gateway unified).

    Union of per-tool ``tool_config.is_hidden`` and capabilities/routes of
    every active plugin with ``meta.tools_hidden``. Used when gateway is OFF
    (and re-applied after turning gateway OFF so selective hides survive).
    Always-visible allowlist is enforced by ``tool_visibility.apply_hidden``.
    """
    from db_layer.models import PluginModel
    from db_layer.tool_config_store import get_hidden_tools
    from core.proxy_tools.tool_visibility import proxy_exposed_name

    names: set[str] = set()
    for pid, tool_name in await get_hidden_tools():
        if tool_name:
            names.add(proxy_exposed_name(pid, tool_name))

    async with get_async_session() as session:
        rows = await session.execute(
            select(PluginModel.id, PluginModel.capabilities, PluginModel.meta).where(
                PluginModel.is_active.is_(True)
            )
        )
        for pid, caps, meta in rows.all():
            if isinstance(meta, dict) and meta.get("tools_hidden"):
                names.update(proxy_exposed_name(pid, c) for c in (caps or []) if c)
                try:
                    from core.plugin_loader.plugin_registry import get_registry
                    routes = get_registry().route_registry.routes_for_plugin(pid)
                    prefix = f"{pid}__"
                    for r in routes:
                        raw_name = (
                            r.operation_id[len(prefix):]
                            if r.operation_id.startswith(prefix)
                            else r.operation_id
                        )
                        names.add(proxy_exposed_name(pid, raw_name))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "collect_selective_hidden_names: routes_for_plugin failed for plugin %s",
                        pid,
                        exc_info=True,
                    )
    return names


async def collect_gateway_hidden_names() -> set[str]:
    """Alias: unified hide set (all non-allowlisted MCP tools)."""
    return await collect_gateway_unified_hide_names()


async def collect_plugin_exposed_tool_names(plugin_id: str) -> set[str]:
    """Resolve FastMCP-exposed tool names for one plugin (routes + capabilities + live proxy)."""
    from db_layer.models import PluginModel
    from core.proxy_tools.tool_visibility import GATEWAY_ALWAYS_VISIBLE, proxy_exposed_name

    names: set[str] = set()
    if not plugin_id:
        return names

    def _add_raw(raw: str) -> None:
        if not raw:
            return
        exposed = proxy_exposed_name(plugin_id, raw)
        if exposed and exposed not in GATEWAY_ALWAYS_VISIBLE:
            names.add(exposed)

    try:
        from core.plugin_loader.plugin_registry import get_registry
        routes = get_registry().route_registry.routes_for_plugin(plugin_id)
        prefix = f"{plugin_id}__"
        for r in routes:
            raw_name = (
                r.operation_id[len(prefix):]
                if r.operation_id.startswith(prefix)
                else r.operation_id
            )
            _add_raw(raw_name)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "collect_plugin_exposed_tool_names: routes_for_plugin failed for %s",
            plugin_id,
            exc_info=True,
        )

    try:
        async with get_async_session() as session:
            row = (await session.execute(
                select(PluginModel.capabilities).where(PluginModel.id == plugin_id)
            )).fetchone()
            if row:
                for c in (row[0] or []):
                    _add_raw(c)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "collect_plugin_exposed_tool_names: capabilities lookup failed for %s",
            plugin_id,
            exc_info=True,
        )

    # Live proxy provider: list upstream tools and map to namespace_tool exposed names.
    # Critical when reapply runs before route registry has the descriptors.
    if plugin_id.startswith("proxy_"):
        proxy_name = plugin_id[len("proxy_"):]
        try:
            from core.context import proxy_manager
            handle = None
            if proxy_manager is not None:
                handle = getattr(proxy_manager, "_active_handles", {}).get(proxy_name)
            if handle is not None:
                server = getattr(handle, "server", None)
                if server is not None and hasattr(server, "list_tools"):
                    tools = await server.list_tools()
                    for t in tools or []:
                        raw = getattr(t, "name", "") or ""
                        _add_raw(raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "collect_plugin_exposed_tool_names: live proxy list failed for %s",
                plugin_id,
                exc_info=True,
            )
    return names


async def collect_selective_hidden_names_for_plugin(plugin_id: str) -> set[str]:
    """Selective-hide names for a single plugin (is_hidden + meta.tools_hidden)."""
    from db_layer.models import PluginModel
    from db_layer.tool_config_store import get_hidden_tools
    from core.proxy_tools.tool_visibility import proxy_exposed_name

    names: set[str] = set()
    for pid, tool_name in await get_hidden_tools():
        if pid == plugin_id and tool_name:
            names.add(proxy_exposed_name(pid, tool_name))

    async with get_async_session() as session:
        row = (await session.execute(
            select(PluginModel.capabilities, PluginModel.meta).where(
                PluginModel.id == plugin_id
            )
        )).fetchone()
        if row:
            caps, meta = row
            if isinstance(meta, dict) and meta.get("tools_hidden"):
                names.update(proxy_exposed_name(plugin_id, c) for c in (caps or []) if c)
                try:
                    from core.plugin_loader.plugin_registry import get_registry
                    routes = get_registry().route_registry.routes_for_plugin(plugin_id)
                    prefix = f"{plugin_id}__"
                    for r in routes:
                        raw_name = (
                            r.operation_id[len(prefix):]
                            if r.operation_id.startswith(prefix)
                            else r.operation_id
                        )
                        names.add(proxy_exposed_name(plugin_id, raw_name))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "collect_selective_hidden_names_for_plugin: routes_for_plugin failed for plugin %s",
                        plugin_id,
                        exc_info=True,
                    )
    return names


async def collect_gateway_hidden_names_for_plugin(plugin_id: str) -> set[str]:
    """Names to re-hide for one plugin after load/remount.

    When gateway unified is ON: every plugin tool (minus allowlist).
    When OFF: only selective is_hidden / tools_hidden flags.
    """
    if await get_gateway_unified():
        return await collect_plugin_exposed_tool_names(plugin_id)
    return await collect_selective_hidden_names_for_plugin(plugin_id)



def _format_catalog_summary_lines(candidates: list[dict]) -> str:
    """Compact catalog lines for MCP instructions (no core_graph dependency).

    Layer-safe stand-in for planner-facing ``_format_candidates`` — summary
    only needs method/path/plugin/op id, not full param hints.
    """
    lines: list[str] = []
    for i, c in enumerate(candidates, 1):
        if not isinstance(c, dict):
            continue
        path = c.get("path") or c.get("path_template") or ""
        lines.append(
            f"{i}. [{c.get('method', '')}] {path}"
            f" [plugin={c.get('plugin_id', '?')}]"
            f" op={c.get('operation_id', '')}"
        )
    return "\n".join(lines)


async def build_tools_summary(limit: int = 40) -> str:
    """Build a compact one-line-per-tool summary of enabled routes for MCP instructions.

    Caps to keep initialize payload small. Does not import ``core_graph``
    (db_layer must stay below the graph package).
    """
    try:
        from db_layer.embeddings.embeddings_routes import list_enabled_routes

        candidates = await list_enabled_routes()
        if not candidates:
            return ""
        capped = candidates[: max(1, int(limit))]
        overview = _format_catalog_summary_lines(capped)
        return "Live tool catalog (use discover_tools for full/filtered): " + overview[:2000]
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("build_tools_summary failed", exc_info=True)
        return ""


async def refresh_tools_summary() -> None:
    """Rebuild compact catalog summary and push into live MCP instructions (no restart)."""
    try:
        from core.context import mcp_context_builder, mcp
        summary = await build_tools_summary()
        if summary:
            # reset to base + new summary (avoid unbounded appends)
            # ContextBuilder keeps list; we rebuild from original base if possible
            base = ""
            try:
                # peek at first instruction as base if present
                if mcp_context_builder.instructions:
                    base = mcp_context_builder.instructions[0]
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("refresh_tools_summary: base instruction peek failed", exc_info=True)
            if base:
                mcp_context_builder.instructions = [base]
            mcp_context_builder.add_context(summary)
            mcp_context_builder.update_mcp_instructions(mcp)
            logger.info("refresh_tools_summary: injected live catalog into MCP instructions")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("refresh_tools_summary failed", exc_info=True)
