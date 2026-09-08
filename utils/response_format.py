"""Response format transforms — Phase 3.8.

Final-stage pipeline step inside ``apply_shape_async`` (step 11). Converts a
shaped list response into a denser wire format (CSV, id_list, summary, flat)
to reduce token overhead before it reaches the LLM context window.

Public API: :func:`apply_format_async` only (async).

Format is selected via ``_response_shape["response_format"]``:
    "csv"       — default; headers once, values per row  (~90 % token reduction for flat lists)
    "json"      — no transform
    "id_list"   — comma-separated IDs only      (~5 tokens/item)
    "summary"   — total count + group breakdown (~30 tokens)
    "flat"      — dot-notation keys, no nesting
    "ui_layout" — validates portfolio UI layout schema
"""

from __future__ import annotations

import asyncio
from collections import Counter
from enum import Enum
from typing import Any, Callable

class ResponseFormat(str, Enum):
    """
    Response formatting utilities.
    """
    JSON = "json"
    CSV = "csv"
    ID_LIST = "id_list"
    SUMMARY = "summary"
    FLAT = "flat"
    UI_LAYOUT = "ui_layout"

def _quote_csv_field(value: str) -> str:
    if any(c in value for c in (',', '"', '\n', '\r')):
        return '"' + value.replace('"', '""') + '"'
    return value


def to_csv(items: list[dict], keys: list[str] | None = None) -> str:
    """
    Converts data to CSV format.
    """
    if not items:
        return ""
    # normalized the item before processing
    normalized_items = [
        item if isinstance(item, dict) else {"value": item} 
        for item in items
    ]
    if keys is None:
        first_item = normalized_items[0]
        keys = sorted(k for k in first_item.keys() if not str(k).startswith("$"))

    header = ",".join(keys)
    rows = [
        ",".join(_quote_csv_field(str(item.get(k, ""))) for k in keys)
        for item in normalized_items
    ]
    return "\n".join([header] + rows)


def to_id_list(items: list[dict], id_key: str = "id") -> str:
    """
    Converts data to an ID list.
    """
    normalized_items = [
        item if isinstance(item, dict) else {"value": item} 
        for item in items
    ]
    return ",".join(str(item[id_key]) for item in normalized_items if item.get(id_key))


def to_summary(items: list[dict], group_by: str | None = None) -> dict[str, Any]:
    """
    Generates a summary of the data.
    """
    result: dict[str, Any] = {"total": len(items)}
    if group_by:
        counts = Counter(
            str(item.get(group_by, "?")) if isinstance(item, dict) else "primitive_value"
            for item in items
        )
        result[f"by_{group_by}"] = dict(counts.most_common(20))
    return result


def to_flat(data: Any, prefix: str = "", sep: str = ".") -> Any:
    """
    Flattens the provided data structure.
    """
    if isinstance(data, list):
        return [to_flat(item, prefix, sep) for item in data]
    if not isinstance(data, dict):
        return data
    out: dict[str, Any] = {}
    for k, v in data.items():
        full_key = f"{prefix}{sep}{k}" if prefix else k
        if isinstance(v, dict):
            out.update(to_flat(v, full_key, sep))
        else:
            out[full_key] = v
    return out


def to_ui_layout(data: Any, shape: dict[str, Any]) -> Any:
    """Validate and clean UI layout payloads."""
    if isinstance(data, dict) and "layout" in data:
        data = data["layout"]

    from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout

    validated, errors = validate_layout(data)
    if validated is not None:
        return validated

    return {
        "status": "error",
        "error": "invalid_ui_layout",
        "details": errors,
    }


FORMAT_PROCESSORS: dict[ResponseFormat, Callable[[list[dict], dict[str, Any]], Any]] = {
    ResponseFormat.CSV: lambda items, shape: to_csv(items),
    ResponseFormat.ID_LIST: lambda items, shape: to_id_list(items, id_key=shape.get("id_key", "id")),
    ResponseFormat.SUMMARY: lambda items, shape: to_summary(items, group_by=shape.get("group_by")),
    ResponseFormat.FLAT: lambda items, shape: to_flat(items),
}

def _apply_format_impl(data: Any, shape: dict[str, Any] | None) -> Any:
    """Sync format implementation (private). Used by :func:`apply_format_async`."""
    fmt_str = (shape or {}).get("response_format", (shape or {}).get("default_response_format", "csv"))
    try:
        fmt = ResponseFormat(fmt_str)
    except ValueError:
        return data

    if fmt == ResponseFormat.JSON:
        return data

    if fmt == ResponseFormat.UI_LAYOUT:
        return to_ui_layout(data, shape or {})

    # Normalise: work with the item list regardless of wrapper shape
    items: list[dict] | None = None
    wrapper_key: str | None = None

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for k, v in data.items():
            if k != "_meta" and isinstance(v, list) and v and isinstance(v[0], dict):
                if items is None or len(v) > len(items):
                    items = v
                    wrapper_key = k

    if items is None:
        return data  # non-list → passthrough

    processor = FORMAT_PROCESSORS.get(fmt)
    if not processor:
        return data

    transformed = processor(items, shape or {})

    if wrapper_key:
        return {**{k: v for k, v in data.items() if k != wrapper_key}, wrapper_key: transformed}
    return transformed


async def apply_format_async(data: Any, shape: dict[str, Any] | None) -> Any:
    """Async final-stage format (step 11 of the response pipeline).

    Only applies to list-shaped data. Reads ``shape["response_format"]`` or
    ``shape["default_response_format"]``; defaults to ``"csv"`` (for lists).
    Offloads CPU-heavy conversion to a worker thread when the payload is large.
    """
    # ~50 KB JSON-ish threshold — same ballpark as response_shape large-response hint.
    try:
        rough = len(str(data)) if data is not None else 0
    except Exception:
        rough = 0
    if rough >= 50_000:
        return await asyncio.to_thread(_apply_format_impl, data, shape)
    return _apply_format_impl(data, shape)
