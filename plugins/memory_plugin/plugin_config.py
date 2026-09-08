"""Plugin-scoped configuration for memory_plugin.

Provides Plugin subclass with lifecycle hooks and manifest constants.
"""

import json
import logging
from pathlib import Path

from core.plugin_loader.plugin_registry import Plugin, PluginContext

logger = logging.getLogger("whiskers.plugins")

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

PLUGIN_ID = _manifest.get("name", Path(__file__).parent.name)


class MemoryPlugin(Plugin):
    """MCP tools for core.memory (agent-callable + harness-shared store)."""

    name = _manifest.get("name", "memory_plugin")
    version = _manifest.get("version", "1.1.0")
    tier = _manifest.get("tier", "free")
    auth_delegate = None
    module_paths = ["plugins.memory_plugin.MCPTools"]
