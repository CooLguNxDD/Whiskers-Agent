"""
Configuration and manifest loading utilities for plugins.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Tuple
from utils.json_processor import resolve_array_string
from utils.response_shape_hints import get_response_hints as _get_core_response_hints

logger = logging.getLogger("whiskers")

# Repo root — two levels up from core/config_loader.py → core/ → repo root
PROJECT_ROOT = Path(__file__).parent.parent

# Plugin-scoped configs keyed by plugin name (from manifest["name"]) or absolute manifest path
PLUGIN_CONFIGS: Dict[str, Dict[str, Any]] = {}
SHAPE_HINTS: Dict[str, str] = {}
FORMAT_HINTS: Dict[str, str] = {}

def _plugin_key(manifest_path: str, manifest: dict | None = None) -> str:
    """Derive a stable key for the plugin."""
    if manifest:
        name = manifest.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    if not manifest_path:
        raise ValueError("manifest_path is required if manifest name is absent")
    return str(Path(manifest_path).resolve())

def get_plugin_config(plugin_key: str) -> dict[str, Any]:
    """Retrieve config for a specific plugin."""
    return dict(PLUGIN_CONFIGS.get(plugin_key, {}))

def get_response_hints(plugin_key: str) -> Tuple[str, str]:
    """Return (shape_hint, format_hint) for a plugin.

    Core owns the canonical shaping/format text (``utils.response_shape_hints``) so it is
    always present regardless of which plugins are loaded; a plugin's config.json
    shape_hint/format_hint (if it still ships one) is appended after the core text
    rather than replacing it — the common case today is an empty plugin-side string.
    """
    core_shape, core_format = _get_core_response_hints()
    plugin_shape = SHAPE_HINTS.get(plugin_key, "")
    plugin_format = FORMAT_HINTS.get(plugin_key, "")
    shape = f"{core_shape}\n\n{plugin_shape}" if plugin_shape else core_shape
    fmt = f"{core_format}\n\n{plugin_format}" if plugin_format else core_format
    return shape, fmt

def load_plugin_config(
    manifest_path: str,
    manifest: dict,
    mcp_context_builder: Any = None,
    mcp: Any = None
) -> dict[str, Any]:
    """Loads plugin config from file, stores it by key, and applies context hints if context objects provided."""
    key = _plugin_key(manifest_path, manifest)
    config_file = manifest.get("config")
    
    if not config_file:
        PLUGIN_CONFIGS.setdefault(key, {})
        SHAPE_HINTS.setdefault(key, "")
        FORMAT_HINTS.setdefault(key, "")
        return {}
        
    config_path = Path(manifest_path).parent / config_file
    
    try:
        if not config_path.exists():
            PLUGIN_CONFIGS.setdefault(key, {})
            SHAPE_HINTS.setdefault(key, "")
            FORMAT_HINTS.setdefault(key, "")
            return {}

        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            
        # Plugin-supplied shape_hint/format_hint (if any) are stored for per-tool
        # description injection only (see get_response_hints) — the canonical
        # server-wide MCP instructions are seeded once from core.utils.response_shape_hints
        # in core/context/_app.py, unconditionally of which plugins load.
        shape_hint = resolve_array_string(config_data.get("shape_hint", ""))
        SHAPE_HINTS[key] = shape_hint

        format_hint = resolve_array_string(config_data.get("format_hint", ""))
        FORMAT_HINTS[key] = format_hint

        PLUGIN_CONFIGS[key] = dict(config_data)
        return config_data

    except Exception as e:
        logger.warning(f"Could not load {config_file} for plugin at {manifest_path}: {e}")
        PLUGIN_CONFIGS.setdefault(key, {})
        SHAPE_HINTS.setdefault(key, "")
        FORMAT_HINTS.setdefault(key, "")
        return {}

def with_response_hints(description: str, plugin_key: str) -> str:
    """Append configured response-shaping and formatting hints to a tool description for a specific plugin."""
    shape, fmt = get_response_hints(plugin_key)
    return f"{description.rstrip()}{shape}{fmt}"
