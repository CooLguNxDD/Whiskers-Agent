"""
Job Search Plugin.

Provides tools for job search, resume enrichment, and application tracking.
"""

import json
import logging
from pathlib import Path

from core.plugin_loader.plugin_registry import Plugin, PluginContext

logger = logging.getLogger("whiskers.plugins")

_manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))

from .plugin_config import JobSearchPlugin


def register(registry):
    """Register job_search_plugin tools and capabilities.

    Imports the MCPTools package to trigger tool decorator side-effects,
    then registers the JobSearchPlugin instance within the registry.
    """
    try:
        from plugins.job_search_plugin import MCPTools  # noqa: F401
        registry.lifecycle.register_plugin(JobSearchPlugin())
        logger.info("job_search_plugin tools registered.")
    except Exception as e:
        logger.error(f"Error registering job_search_plugin: {e}")
        raise
