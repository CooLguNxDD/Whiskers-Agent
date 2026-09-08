"""World Semantic Plugin — H3 spatial store + Unity scene sync + MCP query tools.

Setup: see SETUP_GUIDE.md in this package.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("whiskers.plugins")

try:
    _manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))
except Exception as e:
    logger.error("Failed to load world_semantic_plugin manifest: %s", e)
    _manifest = {}

from .plugin_config import WorldSemanticPlugin  # noqa: E402


def register(registry):
    """Register world_semantic_plugin lifecycle + tools."""
    try:
        from plugins.world_semantic_plugin import MCPTools  # noqa: F401

        registry.lifecycle.register_plugin(WorldSemanticPlugin())
        logger.info("world_semantic_plugin registered.")
    except Exception as e:
        logger.error("Error registering world_semantic_plugin: %s", e)
        raise
