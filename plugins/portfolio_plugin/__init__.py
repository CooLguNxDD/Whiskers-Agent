"""
Portfolio Plugin.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("whiskers.plugins.portfolio")

try:
    _manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))
except Exception as e:
    logger.error(f"Failed to load manifest: {e}")
    _manifest = {}

from .plugin_config import PortfolioPlugin


def register(registry):
    """Register portfolio_plugin and its lifecycle."""
    try:
        from plugins.portfolio_plugin import MCPTools  # noqa: F401
        registry.lifecycle.register_plugin(PortfolioPlugin())
        logger.info("portfolio_plugin registered.")
    except Exception as e:
        logger.error(f"Error registering portfolio_plugin: {e}")
        raise
