"""
Read-only access to a plugin's on-disk ``config.json`` (the "base" config).

The Config tab shows this alongside the console-edited DB override
(``DBPluginRegistry.get_config_override`` / ``set_config_override`` in
db_layer/plugin_registry_store.py). This module never writes to disk —
edits are persisted to the DB override only, matching the skills pattern.
"""

import json
import logging
from pathlib import Path

from core.config_loader import PROJECT_ROOT
from core.plugin_loader import plugin_loader as _plugin_loader

logger = logging.getLogger("whiskers.plugins")


def _safe_plugin_id(plugin_id: str) -> bool:
    """True when plugin_id is a bare directory name (no path traversal)."""
    return bool(plugin_id) and Path(plugin_id).name == plugin_id and ".." not in plugin_id


def resolve_config_filename(plugin_id: str) -> str:
    """Return the config filename declared in the plugin's manifest ("config" key), defaulting to config.json."""
    if not _safe_plugin_id(plugin_id):
        return "config.json"
    manifest = _plugin_loader._relay_manifests.get(plugin_id) or {}
    filename = manifest.get("config") or "config.json"
    # manifest-declared but must stay a bare filename — no traversal outside the plugin dir.
    return Path(filename).name


def load_base_config(plugin_id: str) -> tuple[dict | None, str]:
    """Read the plugin's on-disk config.json. Returns (parsed_dict_or_None, filename).

    Returns (None, filename) if the file is missing or invalid — never raises.
    """
    if not _safe_plugin_id(plugin_id):
        return None, "config.json"
    filename = resolve_config_filename(plugin_id)
    plugins_root = (PROJECT_ROOT / "plugins").resolve()
    config_path = (plugins_root / plugin_id / filename).resolve()
    # Defense-in-depth: resolved path must stay under plugins/
    try:
        if not config_path.is_relative_to(plugins_root):
            return None, filename
    except (ValueError, AttributeError):
        return None, filename

    if not config_path.exists():
        return None, filename
    try:
        raw = config_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("config_file_store: failed to read %s — %s", config_path, exc)
        return None, filename
    if not isinstance(data, dict):
        logger.warning("config_file_store: %s top-level value is not an object", config_path)
        return None, filename
    return data, filename
