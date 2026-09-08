"""Memory Plugin — MCP façade over core.memory.

Exposes tenant-scoped save/search/list/delete plus plan recipe / anti-pattern
search. Storage and embeddings live in ``core.memory`` (shared with the
harness engine); this package only registers MCP tools + skills.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("whiskers.plugins")

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

# Import plugin config (provides MemoryPlugin)
from .plugin_config import MemoryPlugin


def register(registry):
    """Register memory_plugin tools."""
    try:
        from plugins.memory_plugin import MCPTools  # noqa: F401
        registry.lifecycle.register_plugin(MemoryPlugin())
        logger.info("memory_plugin tools registered.")
    except Exception as e:
        logger.error(f"Error registering memory_plugin: {e}")
        raise
