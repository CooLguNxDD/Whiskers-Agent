"""
Plugin-scoped configuration for job_search_plugin.

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


class JobSearchPlugin(Plugin):
    """Static job search plugin.

    Registers job search and detail retrieval tools. Authenticates on each
    provider API call directly using vault keys inline, without a delegator.
    """
    name = _manifest.get("name", "job_search_plugin")
    version = _manifest.get("version", "1.0.0")
    tier = _manifest.get("tier", "free")

    module_paths = ["plugins.job_search_plugin.MCPTools"]
