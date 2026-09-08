"""
Search Plugin.

Provides tools for web search, URL fetch, and semantic content search.
"""

import json
import logging
from pathlib import Path

from core.plugin_loader.plugin_registry import Plugin, PluginContext

logger = logging.getLogger("whiskers.plugins")

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

from .plugin_config import SearchPlugin


def register(registry):
    """Register search_plugin tools and capabilities.

    Imports the MCPTools package to trigger tool decorator side-effects,
    then registers the SearchPlugin instance within the registry.
    """
    try:
        from plugins.search_plugin import MCPTools  # noqa: F401
        registry.lifecycle.register_plugin(SearchPlugin())
        logger.info("search_plugin tools registered.")
    except Exception as e:
        logger.error(f"Error registering search_plugin: {e}")
        raise
