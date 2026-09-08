"""
Plugin-scoped configuration for cat_terminal_relay_plugin.

Static plugin: reuses the default lifecycle (tool collection + route registry)
via ``super().on_load(ctx)``, then registers its two WebSocket legs and starts
the session reaper. Layer-1 inbound auth is owned by core (``auth_delegate`` is unset).
"""

import json
import logging
from pathlib import Path

from core.plugin_loader.plugin_registry import Plugin, PluginContext

logger = logging.getLogger("whiskers.plugins")

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

PLUGIN_ID = _manifest.get("name", Path(__file__).parent.name)
SETTINGS = _manifest.get("settings", {})


class CatTerminalRelayPlugin(Plugin):
    """Reverse WS terminal relay plugin (PRO)."""

    name = _manifest.get("name", "cat_terminal_relay_plugin")
    version = _manifest.get("version", "0.1.0")
    tier = _manifest.get("tier", "pro")
    auth_delegate = None

    async def on_load(self, ctx: PluginContext) -> None:
        """Initialize the plugin by loading standard components and registering WebSocket routes."""
        # Default tail: collect @mcp.tool callables, gate disabled, contribute routes.
        await super().on_load(ctx)
        # Register the two relay legs into the core WS route registry; the
        # entrypoint mounts them onto the Starlette app after http_app() builds.
        from plugins.cat_terminal_relay_plugin.routes import register_routes
        register_routes()

    async def on_ready(self, ctx: PluginContext) -> None:
        """Perform post-load operations: start the reaper, publish the session gauge."""
        await super().on_ready(ctx)
        from plugins.cat_terminal_relay_plugin.services import session_registry
        session_registry.start_reaper()
        # Push the live session count into core telemetry; core must not import us.
        from core.telemetry.collector import collector
        collector.register_gauge_provider("active_sessions", session_registry.active_count)

    async def on_unload(self, ctx: PluginContext) -> None:
        """Stops the session reaper and unloads the plugin."""
        from core.context import http_route_registry
        http_route_registry.unmount_owner("cat_terminal_relay_plugin")
        logger.info("Unmounting routes for plugin: %s", self.name)
        
        from core.telemetry.collector import collector
        collector.unregister_gauge_provider("active_sessions")

        from plugins.cat_terminal_relay_plugin.services import session_registry
        await session_registry.stop_reaper()
        await super().on_unload(ctx)
