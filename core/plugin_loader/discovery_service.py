import json
import logging
import os
import re

from core.plugin_loader.plugin_registry import Tier
from core.plugin_loader.plugin_registry import PluginRegistry

logger = logging.getLogger("whiskers.plugins")

# String alias -> int (canonical tier values; any int is also accepted directly)
_TIER_NAME_MAP: dict[str, int] = {
    "lite": Tier.LITE,
    "free": Tier.LITE,
    "pro": Tier.PRO,
    "admin": Tier.ADMIN,
    "test": Tier.TEST,
}

class PluginDiscoveryService:
    """Service responsible for plugin configuration discovery and tier parsing."""

    @staticmethod
    def parse_tier(tier_value) -> int:
        """Normalize a tier value from JSON config to a plain int.

        Accepts:
          - int : returned as-is — any integer is valid (e.g. 1, 50, 100, 256).
          - str : looked up case-insensitively in _TIER_NAME_MAP (e.g. "Pro" → 100);
                  falls back to Tier.LITE with a warning on no match.
        Returns Tier.LITE (1) by default for any unrecognised or invalid input.
        """
        if isinstance(tier_value, int):
            return tier_value

        if isinstance(tier_value, str):
            value = _TIER_NAME_MAP.get(tier_value.lower().strip())
            if value is not None:
                return value
            logger.warning(
                "Unknown tier alias '%s' — defaulting to 'lite' (%s)",
                tier_value, Tier.LITE,
            )
            return Tier.LITE

        logger.warning("Invalid tier '%s' — defaulting to 'lite' (%s)", tier_value, Tier.LITE)
        return Tier.LITE

    @staticmethod
    def normalize_plugin_name(name: str) -> str:
        """Normalize a plugin identifier to its short name.

        Examples: 'plugins.portfolio_plugin' -> 'portfolio_plugin',
        'portfolio_plugin' -> 'portfolio_plugin', 'plugins.some.pkg' -> 'pkg'
        """
        if not name:
            return name
        if "." in name:
            return name.rsplit(".", 1)[-1]
        return name

    @staticmethod
    def interpolate_manifest(manifest: dict) -> dict:
        """Recursively replace ${VAR} references with os.environ values."""
        var_re = re.compile(r"\$\{([^}]+)\}")

        def _replace(match):
            var = match.group(1)
            val = os.environ.get(var, "")
            if not val:
                logger.warning(
                    "Manifest interpolation: ${%s} resolved to empty string "
                    "(env var not set)", var
                )
            return val

        def _walk(node):
            if isinstance(node, str):
                return var_re.sub(_replace, node)
            if isinstance(node, dict):
                return {k: _walk(v) for k, v in node.items()}
            if isinstance(node, list):
                return [_walk(item) for item in node]
            return node

        return _walk(manifest)

    @classmethod
    def load_config(cls, registry: PluginRegistry, config_path: str) -> list[str]:
        """Loads the plugin config, updates registry tier, and returns the list of packages."""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                registry.config.update(config)
                packages = config.get("plugins", [])
                tier = cls.parse_tier(config.get("tier", "lite"))
                logger.info(f"Plugin config loaded from {config_path}: {len(packages)} packages, system tier: {tier}")
                registry.elevate_tier(tier)
                return packages
        except Exception as e:
            logger.error(f"Failed to read plugin config from {config_path}: {e}")
            return []
