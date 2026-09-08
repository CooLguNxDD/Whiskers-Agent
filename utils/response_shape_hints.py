"""Core-owned prompt text for the response-shaping pipeline.

The shaping pipeline itself (``utils/response_shape.py`` + ``utils/response_format.py``) is
already core and already runs on every stack (direct MCP calls via
``core.context.response_shape_middleware``, proxy calls, ``safe_api_call`` tools, the GOAP
envelope compressor). What used to live outside core was the *documentation* teaching a caller
how to use it — three copies of prose baked into plugin ``config.json`` ``shape_hint``/
``format_hint`` blocks (``core/config_loader.py``), so quarantining/unloading those plugins
silently dropped the instructions even though the pipeline kept running.

This module is the single source of truth for that text, rendered directly from the pipeline's
own vocabulary (``utils.response_shape._PIPELINE_MAP`` keys, ``utils.response_format.ResponseFormat``
members) so the two can't drift apart the way the old hand-written prose did. An operator can
still override either hint via ``config/tools_api_config.json``'s optional top-level ``"hints"``
block — see ``get_shape_hint``/``get_format_hint``.

Leaf module: only imports ``utils.json_processor`` + ``utils.config_registry``. Deliberately
does **not** import ``utils.response_shape`` (which imports ``utils.response_format``) — keeping
the dependency one-way lets the pipeline modules import hints later without a cycle.
"""

from __future__ import annotations

import logging
from typing import Any

from utils.config_registry import get_config_registry
from utils.json_processor import resolve_array_string

logger = logging.getLogger("whiskers")

# ---------------------------------------------------------------------------
# Shaping keys — one entry per key read by utils.response_shape.apply_shape_async's
# _PIPELINE_MAP, in pipeline order. "(default True)" markers mirror
# utils/response_shape.py::_pipeline_step_value.
# ---------------------------------------------------------------------------
_SHAPE_KEYS: tuple[tuple[str, str], ...] = (
    ("limit", "(int) Max array items to return (e.g., 10); truncated items are replaced by a "
              "'... N more items hidden' sentinel."),
    ("project", "(dict) Whitelist specific keys recursively (e.g., {\"id\": true, \"user\": "
                "{\"name\": true}}) — use this to cut tokens as much as possible."),
    ("strip_keys", "(list) Remove specific keys globally (e.g., [\"queueId\", \"retryCount\"])."),
    ("strip_hex", "(bool, default true) Replace >40-char hex strings with `[hash]`."),
    ("strip_empty", "(bool, default true) Remove None, empty strings, lists, and dicts."),
    ("strip_base64", "(bool, default true) Replace base64-looking strings with `[base64 stripped]`."),
    ("dedupe", "(list) Collapse repeated objects by specific keys into `{\"$ref\": id}` "
               "(e.g., [\"user\"])."),
    ("normalize", "(bool, default true) Normalize the raw payload shape before other steps run."),
    ("offload_minio", "(bool, default: on when artifact storage is available) Offload oversized "
                       "leaves to artifact storage, leaving an inline '[offloaded: ... short_id=...]' "
                       "marker plus a fetch_artifact hint."),
    ("include_meta", "(bool) Keep a `_meta` envelope in the output (size, count, sample keys, "
                      "pagination state, applied shape, response_format)."),
    ("meta_only", "(bool) Skip shaping entirely and return only the `_meta` discovery envelope — "
                  "call once without a shape (or with meta_only) to see size/pagination before "
                  "committing to a shape."),
    ("passthrough", "(bool) Opt out of strip/project/limit/format entirely; still offloads "
                     "oversized leaves unless offload_minio is explicitly false."),
)

# ---------------------------------------------------------------------------
# Format keys — one entry per utils.response_format.ResponseFormat member, plus
# the two format-scoped keys (group_by, id_key) that shape those formats.
# ---------------------------------------------------------------------------
_FORMAT_KEYS: tuple[tuple[str, str], ...] = (
    ("json", "full structured JSON output"),
    ("csv", "(default for list payloads) headers once, values only per row (~90% token "
            "reduction for list endpoints)"),
    ("id_list", "comma-separated IDs only (~5 tokens/item, ideal before a fetch-by-id loop); "
                "combine with `\"id_key\": \"<field>\"` if the id field isn't `id`"),
    ("summary", "total count + optional group_by breakdown; combine with `\"group_by\": \"status\"` "
                "in the shape"),
    ("flat", "dot-notation keys, no nesting (reduces bracket overhead)"),
    ("ui_layout", "validates/reshapes a UI layout payload against the portfolio layout schema"),
)

_SHAPE_INTRO = (
    "[RESPONSE SHAPING PIPELINE] Use the optional `_response_shape` dict to reduce token usage.\n"
    "First call without it (or with `meta_only: true`) to get a `_meta` envelope (size, count, "
    "sample keys, pagination state), then use `_response_shape` to filter.\n"
    "Supported shaping keys:"
)
_SHAPE_EXAMPLE = (
    'Example: `"_response_shape": {"limit": 10, "project": {"id": true, "name": true}, '
    '"strip_empty": true}`'
)

_FORMAT_INTRO = (
    "[RESPONSE FORMAT] Set `response_format` inside `_response_shape` to change the list output "
    "structure (default \"csv\"):"
)
_FORMAT_EXAMPLE = (
    'Example: `"_response_shape": {"limit": 10, "response_format": "csv", "include_meta": true}` '
    "limits output to 10 items, formats to CSV, and keeps `_meta`."
)

SHAPE_PARAM_DESCRIPTION = (
    "Optional response shaping override. Result is dense CSV by default; pass "
    '{"response_format": "json"} for structured JSON, or other keys (strip_keys, limit, '
    "project, dedupe) to override the shape."
)


def _render_shape_hint() -> str:
    lines = [_SHAPE_INTRO]
    lines.extend(f"  - `{key}`: {desc}" for key, desc in _SHAPE_KEYS)
    lines.append("")
    lines.append(_SHAPE_EXAMPLE)
    return "\n".join(lines)


def _render_format_hint() -> str:
    lines = [_FORMAT_INTRO]
    lines.extend(f"  \"{key}\" — {desc}" for key, desc in _FORMAT_KEYS)
    lines.append("")
    lines.append(_FORMAT_EXAMPLE)
    return "\n".join(lines)


DEFAULT_SHAPE_HINT: str = _render_shape_hint()
DEFAULT_FORMAT_HINT: str = _render_format_hint()


def _hints_override() -> dict[str, Any]:
    """Read the optional operator override block from tools_api_config.json. Fails soft to {}."""
    try:
        return get_config_registry().tools_api.get("hints", {}) or {}
    except Exception:
        logger.debug("response_shape_hints: override lookup failed", exc_info=True)
        return {}


def get_shape_hint() -> str:
    """Return the shaping hint — an operator override from tools_api_config.json's ``hints``
    block if present, else the pipeline-derived default."""
    override = _hints_override().get("shape_hint")
    if override:
        return resolve_array_string(override)
    return DEFAULT_SHAPE_HINT


def get_format_hint() -> str:
    """Return the format hint — an operator override from tools_api_config.json's ``hints``
    block if present, else the pipeline-derived default."""
    override = _hints_override().get("format_hint")
    if override:
        return resolve_array_string(override)
    return DEFAULT_FORMAT_HINT


def get_response_hints() -> tuple[str, str]:
    """Return (shape_hint, format_hint) — the core-owned pair every stack should inject."""
    return get_shape_hint(), get_format_hint()


def reload_hints() -> None:
    """Re-read tools_api_config.json's override block (useful for tests / hot-reload).

    The default hints are pure functions of the module-level tables above and need no reload;
    this only affects the operator-override path since get_config_registry() is a process-wide
    singleton read fresh on every call already — kept for symmetry with
    utils.response_shape.reload_shapes() and as an explicit hook for tests that patch the
    registry.
    """
    logger.info("response_shape_hints: override read fresh on next call (no cached state)")
