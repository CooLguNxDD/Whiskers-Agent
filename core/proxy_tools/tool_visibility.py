"""
tool_visibility — gate FastMCP exposure of individual tools.

Backs the per-tool MCP toggle (``tool_config`` table, core_017). Disabling a
tool here removes it from ``mcp.list_tools()`` and rejects ``call_tool`` for
MCP clients, while leaving the tool registered in code and its route embedding
intact. The route/semantic toggle (``route_embeddings.is_enabled``) is a
separate concern handled by the embedder/search path.

Implementation: FastMCP 3.x exposes ``server.disable(names=..., components=...)``
and ``server.enable(...)`` which append reversible visibility transforms
(later transform wins). We drive those by tool name — tool names are globally
unique in a single FastMCP registry, so ``plugin_id`` is only used for the
DB key and API path, not for the FastMCP operation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

from core.proxy_tools.fastmcp_adapter import (
    disable_tool as _disable_tool,
    enable_tool as _enable_tool,
)
from utils.server_config import GATEWAY_ALWAYS_VISIBLE  # noqa: F401 — re-exported

logger = logging.getLogger("whiskers")

_TOOL_COMPONENT = {"tool"}

# GATEWAY_ALWAYS_VISIBLE is imported from utils.server_config; re-exported here
# for backward-compat with callers that do:
#   from core.proxy_tools.tool_visibility import GATEWAY_ALWAYS_VISIBLE
# The value is determined at startup from server_config.json → gateway.always_visible_tools
# (falling back to the hardcoded default defined in utils.server_config).


class ToolVisibility:
    """Hide/show MCP tools via FastMCP visibility transforms."""

    def __init__(self, mcp_app: Any | None = None) -> None:
        """Initialize ToolVisibility with an optional FastMCP app instance."""
        self._mcp = mcp_app

    def bind(self, mcp_app: Any) -> None:
        """Attach the FastMCP instance (called once from ``core.context``)."""
        self._mcp = mcp_app

    def hide(self, tool_name: str) -> bool:
        """Remove ``tool_name`` from MCP exposure. Returns False if unbound."""
        if self._mcp is None or not tool_name:
            return False
        _disable_tool(self._mcp, {tool_name}, _TOOL_COMPONENT)
        self._broadcast("hide", tool_name)
        logger.info("tool_visibility: hid tool '%s' from MCP exposure", tool_name)
        return True

    def show(self, tool_name: str) -> bool:
        """Restore ``tool_name`` to MCP exposure. Returns False if unbound."""
        if self._mcp is None or not tool_name:
            return False
        _enable_tool(self._mcp, {tool_name}, _TOOL_COMPONENT)
        self._broadcast("show", tool_name)
        logger.info("tool_visibility: restored tool '%s' to MCP exposure", tool_name)
        return True

    def _broadcast(self, action: str, tool_name: str) -> None:
        """Fire the cross-process NOTIFY as a supervised task; never blocks the caller."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no running loop (e.g. sync test context) — local change still applied
        from core.clustering.registry_sync import broadcast_tool_visibility_change
        from utils.tasks import spawn_supervised
        spawn_supervised(
            broadcast_tool_visibility_change(action, tool_name),
            name=f"tool_visibility_broadcast:{action}:{tool_name}",
        )

    def apply_persisted(self, disabled_keys: Iterable[tuple[str, str]]) -> int:
        """Hide every persisted-disabled tool in one transform. Returns count.

        ``disabled_keys`` is the ``(plugin_id, tool_name)`` set from
        ``tool_config_store.get_disabled_tools()``.
        """
        names = {
            proxy_exposed_name(pid, tool_name)
            for (pid, tool_name) in disabled_keys
            if tool_name
        }
        if not names or self._mcp is None:
            return 0
        _disable_tool(self._mcp, names, _TOOL_COMPONENT)
        return len(names)


    def apply_hidden(self, hidden_names: Iterable[str]) -> int:
        """Hide every gateway-hidden tool in one transform. Returns count.

        Skips :data:`GATEWAY_ALWAYS_VISIBLE` so the unified entry point and
        auth tools survive. Hidden tools vanish from ``list_tools`` / reject
        direct ``call_tool`` but stay reachable internally by ``run_graph``.
        """
        names = {n for n in hidden_names if n and n not in GATEWAY_ALWAYS_VISIBLE}
        if not names or self._mcp is None:
            return 0
        _disable_tool(self._mcp, names, _TOOL_COMPONENT)
        logger.info("tool_visibility: gateway-hid %d tool(s) from MCP exposure", len(names))
        return len(names)

    def hide_gateway(self, tool_name: str) -> bool:
        """Gateway-hide one tool live (skips always-visible). Returns False if unbound/skipped."""
        if tool_name in GATEWAY_ALWAYS_VISIBLE:
            return False
        return self.hide(tool_name)

    def show_gateway(self, tool_name: str) -> bool:
        """Restore one gateway-hidden tool live. Returns False if unbound."""
        return self.show(tool_name)


def proxy_exposed_name(plugin_id: str, tool_name: str) -> str:
    """Resolve the FastMCP-exposed name for a plugin's tool."""
    if plugin_id.startswith("proxy_"):
        proxy_name = plugin_id[len("proxy_"):]
        return f"{proxy_name}_{tool_name}"
    return tool_name


async def hide_proxy_tools_if_gateway(proxy_name: str, tools: Iterable[Any]) -> int:
    """If run_graph_unified is ON, hide namespaced proxy tools from MCP exposure.

    ``tools`` is the upstream tool list (raw names). Exposed MCP names are
    ``{proxy_name}_{raw}`` via :func:`proxy_exposed_name`. Safe no-op when
    gateway is OFF or tools is empty. Returns count applied.
    """
    from db_layer.gateway_settings_store import get_gateway_unified
    from core.context import tool_visibility as _tv

    if not proxy_name or not await get_gateway_unified():
        return 0
    plugin_id = f"proxy_{proxy_name}"
    names = {
        proxy_exposed_name(plugin_id, getattr(t, "name", "") or "")
        for t in (tools or [])
        if getattr(t, "name", None)
    }
    names = {n for n in names if n and n not in GATEWAY_ALWAYS_VISIBLE}
    if not names:
        return 0
    count = _tv.apply_hidden(names)
    logger.info(
        "tool_visibility: gateway-hid %d proxy tool(s) for namespace '%s'",
        count, proxy_name,
    )
    return count


async def reapply_hidden_for_plugin(plugin_id: str) -> None:
    """Re-apply visibility settings for a single plugin's tools after load/remount.

    Always re-hides disabled tools. When gateway unified is ON, hides every
    tool for this plugin (minus allowlist). When OFF, only selective
    ``is_hidden`` / ``meta.tools_hidden`` flags.
    """
    from core.context import tool_visibility
    if tool_visibility._mcp is None:
        return

    from db_layer.tool_config_store import get_disabled_tools
    from db_layer.gateway_settings_store import collect_gateway_hidden_names_for_plugin

    # 1. Apply disabled tools
    try:
        disabled_keys = await get_disabled_tools()
        names = {
            proxy_exposed_name(pid, tname)
            for (pid, tname) in disabled_keys
            if pid == plugin_id and tname
        }
        if names:
            _disable_tool(tool_visibility._mcp, names, _TOOL_COMPONENT)
            logger.info("reapply_hidden_for_plugin: hid %d disabled tool(s) for %s", len(names), plugin_id)
    except Exception as exc:
        logger.warning("reapply_hidden_for_plugin: failed to apply disabled tools for %s: %s", plugin_id, exc)

    # 2. Gateway unified (all plugin tools) or selective is_hidden / tools_hidden
    try:
        hidden_names = await collect_gateway_hidden_names_for_plugin(plugin_id)
        if hidden_names:
            tool_visibility.apply_hidden(hidden_names)
            logger.info(
                "reapply_hidden_for_plugin: gateway/selective-hid %d tool(s) for %s",
                len(hidden_names), plugin_id,
            )
    except Exception as exc:
        logger.warning(
            "reapply_hidden_for_plugin: failed to apply gateway/selective hidden for %s: %s",
            plugin_id, exc,
        )

