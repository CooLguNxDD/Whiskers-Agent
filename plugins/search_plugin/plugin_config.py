"""
Plugin-scoped configuration for search_plugin.

Provides Plugin subclass with lifecycle hooks and manifest constants.
"""

import json
import logging
from pathlib import Path

from core.plugin_loader.plugin_registry import Plugin, PluginContext

logger = logging.getLogger("whiskers.plugins")

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

PLUGIN_ID = _manifest.get("name", Path(__file__).parent.name)
SETTINGS = _manifest.get("settings", {})


class SearchPlugin(Plugin):
    """Static search plugin.

    Registers search and content retrieval tools. Free-form ``semantic_index``
    collections still write to ``search_content_vectors``; declared
    ``memory`` namespaces are registered into the core MemoryRegistry.
    """

    name = _manifest.get("name", "search_plugin")
    version = _manifest.get("version", "1.1.0")
    tier = _manifest.get("tier", "free")

    module_paths = ["plugins.search_plugin.MCPTools"]

    async def on_load(self, ctx: PluginContext) -> None:
        """Register optional runtime search namespaces into MemoryRegistry."""
        await super().on_load(ctx)
        # Free-form search collections remain allowed on the search store;
        # registry entry documents the default plugin namespace for agents.
        try:
            ctx.contribute_memory(
                [
                    {
                        "name": "default",
                        "backend": "search",
                        "description": "Default semantic search index",
                        "writable": True,
                    }
                ]
            )
        except Exception as exc:
            logger.debug("search_plugin contribute_memory skipped: %s", exc)
