"""Config loader for tools_api_config.json.

Loads once at import time.  Exposes endpoint meta (base + per-tool overrides)
and the raw response_shapes / settings blocks.

endpoint_meta resolution
------------------------
Every tool gets at minimum the ``_base`` defaults.  Per-tool entries are
shallow-merged on top as overrides.  Call ``get_endpoint_meta(tool_name)``
instead of ``ENDPOINT_META.get(name, {})``.
"""

from __future__ import annotations

import logging
from typing import Any

from utils.config_registry import get_config_registry

logger = logging.getLogger("whiskers")

TOOLS_CONFIG: dict[str, Any] = get_config_registry().tools_api

# ---------------------------------------------------------------------------
# Top-level sections
# ---------------------------------------------------------------------------
SETTINGS: dict[str, Any] = TOOLS_CONFIG.get("settings", {})
RESPONSE_SHAPES: dict[str, Any] = TOOLS_CONFIG.get("response_shapes", {})

# ---------------------------------------------------------------------------
# Endpoint meta — base + per-tool overrides
# ---------------------------------------------------------------------------
_RAW_ENDPOINT_META: dict[str, Any] = TOOLS_CONFIG.get("endpoint_meta", {})
_ENDPOINT_META_BASE: dict[str, Any] = _RAW_ENDPOINT_META.get("_base", {})
ENDPOINT_META: dict[str, Any] = {
    k: v for k, v in _RAW_ENDPOINT_META.items() if k != "_base"
}


def get_endpoint_meta(tool_name: str) -> dict[str, Any]:
    """Return merged endpoint meta for *tool_name* (base defaults + any override).

    Every tool gets at minimum the ``_base`` values; per-tool entries win on
    conflict.  Returns a fresh dict so callers can safely mutate it.
    """
    override = ENDPOINT_META.get(tool_name, {})
    return {**_ENDPOINT_META_BASE, **override}
