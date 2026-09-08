"""Central eager loader for JSON config files under ``config/``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("whiskers")

_ROOT = Path(__file__).parent.parent
_CONFIG = _ROOT / "config"

PLUGIN_CONFIG_PATH = str(_CONFIG / "plugin_config.json")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    """Load a JSON config file, returning ``{}`` on missing or invalid content."""
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        logger.debug("Loaded %s", label)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("%s not loaded: %s", label, exc)
        return {}


class ConfigRegistry:
    """Singleton holder for eagerly loaded config dicts."""

    def __init__(self) -> None:
        self.server = _load_json(_CONFIG / "server_config.json", "server_config.json")
        self.tools_api = _load_json(_CONFIG / "tools_api_config.json", "tools_api_config.json")
        self.embedding = _load_json(_CONFIG / "embedding_config.json", "embedding_config.json")


_registry_instance: ConfigRegistry | None = None


def get_config_registry() -> ConfigRegistry:
    """Return the shared config registry singleton."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ConfigRegistry()
    return _registry_instance