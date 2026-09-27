"""Plugin-scoped configuration for cat_fleet_chat_plugin."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.plugin_loader.plugin_registry import Plugin

logger = logging.getLogger("whiskers.plugins")

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

PLUGIN_ID = _manifest.get("name", Path(__file__).parent.name)
SETTINGS = _manifest.get("settings", {})


class CatFleetChatPlugin(Plugin):
    """MCP proxy onto the standalone Cat Fleet Chat hub. Owns no tables."""

    name = _manifest.get("name", "cat_fleet_chat_plugin")
    version = _manifest.get("version", "0.1.0")
    tier = _manifest.get("tier", "free")
    auth_delegate = None
    module_paths = ["plugins.cat_fleet_chat_plugin.MCPTools"]

    async def on_load(self, ctx) -> None:
        """Open the shared hub client, then register tools."""
        from plugins.cat_fleet_chat_plugin.hub_client import open_client

        await open_client()
        await super().on_load(ctx)

    async def on_unload(self, ctx) -> None:
        """Close the hub client so a reload does not leak sockets."""
        from plugins.cat_fleet_chat_plugin.hub_client import close_client

        try:
            await close_client()
        finally:
            await super().on_unload(ctx)
