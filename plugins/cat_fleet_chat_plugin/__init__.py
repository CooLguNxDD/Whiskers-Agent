"""Cat Fleet Chat plugin — MCP proxy onto the standalone hub."""

import logging

logger = logging.getLogger("whiskers.plugins")

from .plugin_config import CatFleetChatPlugin


def register(registry):
    """Register cat_fleet_chat_plugin tools."""
    try:
        from plugins.cat_fleet_chat_plugin import MCPTools  # noqa: F401

        registry.lifecycle.register_plugin(CatFleetChatPlugin())
        logger.info("cat_fleet_chat_plugin tools registered.")
    except Exception as exc:
        logger.error(f"Error registering cat_fleet_chat_plugin: {exc}")
        raise
