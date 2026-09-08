import logging
from typing import List

from core.plugin_loader import resolver
from core.plugin_loader.plugin_registry import PluginRegistry

logger = logging.getLogger("whiskers.plugins")

class PluginDependencyResolver:
    """Service responsible for dependency ordering of plugins."""

    @staticmethod
    def resolve_plan(packages: List[str], registry: PluginRegistry, excluded: set[str] = None):
        """Resolves the plugin DAG plan using the pure resolver."""
        try:
            return resolver.resolve(packages, registry.system_tier, excluded=excluded)
        except Exception as exc:
            logger.error("Failed to resolve plugin DAG plan: %s", exc)
            return None
