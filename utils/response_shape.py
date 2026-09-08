"""Response shaping pipeline driven by tools_api_config.json.

Loaded once at import time. **Async only** — call ``apply_shape_async`` (via
``safe_api_call`` / proxy loader / envelope sanitize) so large string leaves
hit MinIO **before** strip/CSV.

Pipeline (when shape is provided):
  normalize → offload_minio → strip_* → dedupe → project → limit → format

Shape config lives in ``tools_api_config.json`` under ``"response_shapes"``,
keyed by operationId. ``offload_minio`` defaults to True when artifact offload
is enabled and MinIO is available; set false per-op or per-call to skip.

**Tool-boundary return contract is always flat.** ``apply_shape_async``
returns a bare list/dict — never a ``{"_meta": ..., "data": ...}`` envelope —
unless the caller explicitly sets ``include_meta: true`` on the shape (or
uses ``meta_only``/no-shape discovery mode). This matters because every
downstream GOAP consumer (arg-binding resolver, ``validator_node``,
``fold_working_memory``, ``collect_fetched_ids``, graph finalize) expects a
flat payload; a status-less ``{_meta, data}`` result would get double-wrapped
by ``validator_node`` into ``data.data`` and pollute id-harvesting with
``_meta`` scalars. When MinIO offload fires without ``include_meta``, refs
still surface via the inline ``"[offloaded: ... short_id=...]"`` marker left
in the data plus the ``"artifacts.offloaded"`` EventBus notify — the envelope
wrap is not needed to retrieve them.

    {
      "response_shapes": {
        "list_form_entries": {
          "limit": 50,
          "project": { "id": true, "record_data": true, "form": {"id": true} },
          "strip_keys": ["queueId", "retryCount"],
          "strip_hex": true,
          "dedupe": ["user", "form"]
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from utils.json_processor import extract_lists, flatten_dict
from utils.response_format import apply_format_async
from utils.tools_api_config import RESPONSE_SHAPES as _SHAPES, SETTINGS

logger = logging.getLogger("whiskers")

_HEX_RE = re.compile(r"^[0-9a-f]{40,}$", re.IGNORECASE)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/\-_]+=*$")


# Minimum base64 string length — configurable via tools_api_config.json settings.base64_min_len
_BASE64_MIN_LEN: int = SETTINGS.get("base64_min_len", 128)


# Caps ancestry depth to prevent stack overflows on pathologically nested or self-referencing containers.
_MAX_STRIP_DEPTH = 50


def strip_base64_fields(
    data: Any,
    placeholder: str = "[base64 stripped]",
    seen: set[int] | None = None,
    depth: int = 0,
) -> Any:
    """Recursively replace base64-looking strings with *placeholder*.

    Cycle-safe: tracks container ids on the current recursion path so a self-referencing dict/list returns a circular-reference marker instead of recursing forever, and bails out past _MAX_STRIP_DEPTH on pathologically deep structures."""
    if seen is None:
        seen = set()
    if depth > _MAX_STRIP_DEPTH:
        return data

    if isinstance(data, dict):
        obj_id = id(data)
        if obj_id in seen:
            return "[circular reference]"
        seen.add(obj_id)
        try:
            return {k: strip_base64_fields(v, placeholder, seen, depth + 1) for k, v in data.items()}
        finally:
            seen.discard(obj_id)
    elif isinstance(data, (list, tuple)):
        obj_id = id(data)
        if obj_id in seen:
            return "[circular reference]"
        seen.add(obj_id)
        try:
            return [strip_base64_fields(item, placeholder, seen, depth + 1) for item in data]
        finally:
            seen.remove(obj_id)
    elif isinstance(data, str):
        if data.startswith("data:") or (len(data) >= _BASE64_MIN_LEN and _BASE64_RE.match(data)):
            return placeholder
        return data
    return data


# ---------------------------------------------------------------------------
# Internal transform steps
# ---------------------------------------------------------------------------

def _limit(data: Any, n: int) -> Any:
    """Soft-cap a list: truncate to n and append a '… N more items hidden' sentinel when truncated."""
    def _soft_cap(lst: list) -> list:
        if len(lst) <= n:
            return lst
        remaining = len(lst) - n
        label = "item" if remaining == 1 else "items"
        # Truncate and add a descriptive placeholder for hidden items
        return lst[:n] + [f"… {remaining} more {label} hidden, try use pagination or increase limit to see more."]

    # dict process
    def _limit_dict(d: dict) -> dict:
        for key in ("items", "data", "results", "rows"):
            if isinstance(d.get(key), list):
                # Apply cap to the first list-like key found
                d = dict(d)
                d[key] = _soft_cap(d[key])
                return d
        return d

    _LIMIT_MAP = {
        list: _soft_cap,
        dict: _limit_dict,
    }

    handler = _LIMIT_MAP.get(type(data))
    return handler(data) if handler else data


def _project_dict(data: dict, spec: dict[str, Any]) -> dict:
    result: dict[str, Any] = {}
    for key, rule in spec.items():
        if key not in data:
            continue
        if rule is True:
            # Keep the value as is if rule is True
            result[key] = data[key]
        elif type(rule) is dict:
            # Nested projection for complex objects
            result[key] = _project(data[key], rule)
    return result

_PROJECT_MAP = {
    list: lambda data, spec: [_project(item, spec) for item in data],
    dict: _project_dict,
}

def _project(data: Any, spec: dict[str, Any]) -> Any:
    """Recursively whitelist keys according to spec.

    spec values:
    - ``True``  → keep the key as a leaf (no further filtering)
    - ``dict``  → recurse into the value using the nested spec
    """
    handler = _PROJECT_MAP.get(type(data))
    return handler(data, spec) if handler else data


_STRIP_KEYS_MAP = {
    list: lambda data, keys: [_strip_keys(item, keys) for item in data],
    dict: lambda data, keys: {
        k: _strip_keys(v, keys)
        for k, v in data.items()
        if k not in keys
    },
}

def _strip_keys(data: Any, keys: set[str]) -> Any:
    """Recursively remove blacklisted keys from all dicts."""
    handler = _STRIP_KEYS_MAP.get(type(data))
    return handler(data, keys) if handler else data


_STRIP_HEX_MAP = {
    list: lambda data, ph: [_strip_hex(item, ph) for item in data], # Recurse into list items
    dict: lambda data, ph: {k: _strip_hex(v, ph) for k, v in data.items()}, # Filter keys and recurse into values
    str: lambda data, ph: ph if _HEX_RE.match(data) else data,
}

def _strip_hex(data: Any, placeholder: str = "[hash]") -> Any:
    """Recursively replace 40+ char hex strings with a placeholder."""
    handler = _STRIP_HEX_MAP.get(type(data))
    return handler(data, placeholder) if handler else data


def _strip_empty_dict(data: dict) -> dict:
    result: dict[str, Any] = {}
    for k, v in data.items():
        cleaned = _strip_empty(v)
        # Skip values that are None or effectively empty
        if cleaned is None or cleaned == "" or cleaned == [] or cleaned == {}:
            continue
        result[k] = cleaned
    return result

_STRIP_EMPTY_MAP = {
    list: lambda data: [_strip_empty(item) for item in data],
    dict: _strip_empty_dict,
}

def _strip_empty(data: Any) -> Any:
    """Recursively remove None, empty string, empty list, and empty dict values."""
    handler = _STRIP_EMPTY_MAP.get(type(data))
    return handler(data) if handler else data


def _normalize(data: Any, include_meta: bool = False) -> Any:
    """Extract the primary record list from a nested response structure,
    optionally broadcasting root-level metadata into each record.
    """
    if isinstance(data, list):
        return data  # already a list — pass through
        
    if isinstance(data, dict):
        lists = extract_lists(data)  # sorted largest-first, ignores _meta
        
        if lists:
            primary_list = []
            for lst in lists:
                primary_list.extend(lst)
            
            if include_meta:
                #Isolate the metadata by grabbing everything that IS NOT a list
                meta_raw = {k: v for k, v in data.items() if type(v) is not list and k != "_meta"}
                
                #Flatten the metadata (e.g., 'totalCount': {'count': 10} -> 'totalCount_count': 10)
                flat_meta = flatten_dict(meta_raw)
                
                #Inject the flattened metadata into every row of the primary list
                for row in primary_list:
                    if isinstance(row, dict):
                        # Keep it as a nested _meta field so it survives projection steps
                        if "_meta" not in row:
                            row["_meta"] = flat_meta.copy()
                        else:
                            # Prioritize existing _meta keys if there's a collision
                            for key, val in flat_meta.items():
                                if key not in row["_meta"]:
                                    row["_meta"][key] = val
            return primary_list
            
        # Fallback if no list is found
        core = {k: v for k, v in data.items() if k != "_meta"}
        if core:
            return [flatten_dict(core)]  # single dict → flatten into 1-item list
            
    return data


def _dedupe(data: Any, keys: list[str]) -> Any:
    """Collapse repeated nested objects (by .id) into {"$ref": id} stubs.

    Only collapses objects that have appeared more than once across the
    response, keyed by the field names listed in ``keys``.
    """
    if type(data) not in (list, dict):
        return data

    # First pass: count occurrences of each (field_name, id) pair.
    seen: dict[str, dict[Any, int]] = {k: {} for k in keys}

    def _count_dict(node: dict) -> None:
        for field in keys:
            val = node.get(field)
            if type(val) is dict and "id" in val:
                # Track how many times each object ID appears under this field name
                seen[field][val["id"]] = seen[field].get(val["id"], 0) + 1
        for v in node.values():
            _count(v)

    _COUNT_MAP = {
        list: lambda node: [_count(item) for item in node],
        dict: _count_dict,
    }

    def _count(node: Any) -> None:
        handler = _COUNT_MAP.get(type(node))
        if handler:
            handler(node)

    _count(data)

    # Determine which ids are actually duplicated.
    dupes: dict[str, set[Any]] = {
        field: {oid for oid, cnt in counts.items() if cnt > 1}
        for field, counts in seen.items()
    }
    first_seen: dict[str, set[Any]] = {k: set() for k in keys}

    def _collapse_dict(node: dict) -> dict:
        result: dict[str, Any] = {}
        for k, v in node.items():
            if k in keys and type(v) is dict and "id" in v:
                oid = v["id"]
                if oid in dupes[k]:
                    if oid in first_seen[k]:
                        # Already emitted full object; replace trailing ones with a reference
                        result[k] = {"$ref": oid}
                        continue
                    # First time seeing this dupe; mark it so subsequent ones are collapsed
                    first_seen[k].add(oid)
            result[k] = _collapse(v)
        return result

    _COLLAPSE_MAP = {
        list: lambda node: [_collapse(item) for item in node],
        dict: _collapse_dict,
    }

    def _collapse(node: Any) -> Any:
        handler = _COLLAPSE_MAP.get(type(node))
        return handler(node) if handler else node

    return _collapse(data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_LARGE_RESPONSE_THRESHOLD = 50_000  # bytes

# Marker key for offload_minio — handled specially in apply_shape_async (await).
_OFFLOAD_MINIO_KEY = "offload_minio"

ARTIFACT_HINT = (
    "Large fields offloaded to MinIO; an inline preview is usually present on the marker. "
    "Prefer the preview for status/summary. Call fetch_artifact(short_id=...) ONLY if the "
    "preview is insufficient for your next step. Console: /api/artifacts/session_gated/{short_id}"
)

# Pipeline order (11 steps when shaped):
#  1 normalize → 2 offload_minio → 3–9 strip/dedupe/project/limit
#  → 10 include_meta → 11 apply_format_async
# offload_minio transform is async (handled in apply_shape_async).
_PIPELINE_MAP = {
    "normalize":    lambda data, v, incl_meta=False: _normalize(data, incl_meta) if v else data,   # step 1
    "offload_minio": None,  # step 2 — MinIO large-leaf offload (async; before strip)
    "strip_base64": lambda data, v: strip_base64_fields(data) if v else data,  # step 3
    "strip_hex":    lambda data, v: _strip_hex(data) if v else data,  # step 4
    "strip_keys":   lambda data, keys: _strip_keys(data, set(keys)),  # step 5
    "strip_empty":  lambda data, v: _strip_empty(data) if v else data,  # step 6
    "dedupe":       _dedupe,  # step 7
    "project":      _project,  # step 8
    "limit":        _limit,  # step 9
}


def _minio_offload_default() -> bool:
    """True when artifact offload is enabled and MinIO is reachable."""
    try:
        from utils.server_config import ARTIFACT_OFFLOAD_ENABLED
        from core.artifact_store.minio_client import minio_available

        return bool(ARTIFACT_OFFLOAD_ENABLED and minio_available())
    except Exception as exc:
        logger.debug("MinIO offload default check failed, defaulting off: %s", exc, exc_info=True)
        return False


def _resolve_tenant_id() -> int:
    try:
        from core.context import current_tenant_id

        return int(current_tenant_id.get() or 1)
    except Exception as exc:
        logger.debug("Failed to resolve tenant_id, defaulting to 1: %s", exc, exc_info=True)
        return 1


def _resolve_session_id(request_params: dict | None, session_id: str | None) -> str | None:
    if session_id:
        return session_id
    if not request_params:
        return None
    for key in ("session_id", "sessionId", "thread_id"):
        val = request_params.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _public_offload_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip internal MinIO keys for _meta / event payloads."""
    out: list[dict[str, Any]] = []
    for a in refs:
        if not isinstance(a, dict) or not a.get("short_id"):
            continue
        sid = a.get("short_id")
        out.append(
            {
                "kind": a.get("kind"),
                "short_id": sid,
                "bytes": a.get("bytes"),
                "content_type": a.get("content_type"),
                "session_id": a.get("session_id"),
                "path": a.get("path") or a.get("source_path"),
                "console_path": a.get("console_path")
                or f"/api/artifacts/session_gated/{sid}",
            }
        )
    return out


def _emit_artifacts_offloaded(
    *,
    tool_name: str,
    tenant_id: int,
    session_id: str | None,
    refs: list[dict[str, Any]],
    source: str = "response_shape",
) -> None:
    """Fire-and-forget EventBus notify after successful offload (best-effort)."""
    public = _public_offload_refs(refs)
    if not public:
        return
    payload = {
        "source": source,
        "tool_name": tool_name,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "artifacts": public,
        "hint": ARTIFACT_HINT,
    }
    try:
        from core.plugin_loader.plugin_registry import get_registry

        get_registry().events.emit("artifacts.offloaded", payload)
    except Exception:
        logger.debug("artifacts.offloaded emit skipped", exc_info=True)


async def _run_offload_minio(
    data: Any,
    *,
    tool_name: str,
    session_id: str | None,
    tenant_id: int,
    min_bytes: int | None = None,
    max_artifacts: int | None = None,
    source: str = "response_shape",
) -> tuple[Any, list[dict[str, Any]]]:
    """Upload large string leaves to MinIO; return (slim_data, refs). Fail-safe."""
    try:
        from core.artifact_store.store import extract_large_artifacts
        from utils.server_config import (
            ARTIFACT_OFFLOAD_MAX_PER_ROUND,
            ARTIFACT_OFFLOAD_MIN_FIELD_BYTES,
        )

        eff_min = ARTIFACT_OFFLOAD_MIN_FIELD_BYTES if min_bytes is None else min_bytes
        eff_max = ARTIFACT_OFFLOAD_MAX_PER_ROUND if max_artifacts is None else max_artifacts
        slim, refs = await extract_large_artifacts(
            data,
            session_id=session_id,
            tenant_id=tenant_id,
            min_bytes=eff_min,
            max_artifacts=eff_max,
            path_root="data",
        )
        if refs:
            _emit_artifacts_offloaded(
                tool_name=tool_name,
                tenant_id=tenant_id,
                session_id=session_id,
                refs=refs,
                source=source,
            )
        return slim, refs
    except Exception as exc:
        logger.warning(
            "response_shape offload_minio failed (leave data intact): %s", exc, exc_info=True
        )
        return data, []


async def offload_structured_payload(
    payload: Any,
    *,
    tool_name: str = "",
    request_params: dict | None = None,
    session_id: str | None = None,
    min_bytes: int | None = None,
    max_artifacts: int | None = None,
    source: str = "response_shape",
) -> tuple[Any, list[dict[str, Any]]]:
    """Offload large string leaves from a structured dict payload. Fail-safe.

    Thin wrapper over ``_run_offload_minio`` so structured offload always emits
    ``artifacts.offloaded`` (same as the shape pipeline / text path).
    """
    if not isinstance(payload, dict):
        return payload, []
    tenant_id = _resolve_tenant_id()
    sid = _resolve_session_id(request_params, session_id)
    return await _run_offload_minio(
        payload,
        tool_name=tool_name,
        session_id=sid,
        tenant_id=tenant_id,
        min_bytes=min_bytes,
        max_artifacts=max_artifacts,
        source=source,
    )


async def offload_text_body(
    text: Any,
    *,
    tool_name: str = "",
    request_params: dict | None = None,
    session_id: str | None = None,
    min_bytes: int | None = None,
    max_artifacts: int | None = None,
    source: str = "response_shape",
) -> tuple[str, dict | None, list[dict[str, Any]]]:
    """Offload one text body: JSON leaf walk, else whole-string. Fail-safe.

    Single text-offload policy used by ArtifactOffloadMiddleware content-block
    walk and any other caller that has a large string rather than a dict tree.

    Returns ``(new_text, structured_override | None, real_refs)``. Refs always
    come from the store (include ``short_id``) — never fabricated.
    """
    if not isinstance(text, str):
        return text, None, []  # type: ignore[return-value]

    from core.artifact_store.store import is_offload_marker
    from utils.server_config import (
        ARTIFACT_OFFLOAD_MAX_PER_ROUND,
        ARTIFACT_OFFLOAD_MIN_FIELD_BYTES,
    )

    eff_min = ARTIFACT_OFFLOAD_MIN_FIELD_BYTES if min_bytes is None else min_bytes
    eff_max = ARTIFACT_OFFLOAD_MAX_PER_ROUND if max_artifacts is None else max_artifacts

    if is_offload_marker(text) or len(text) < eff_min or eff_max <= 0:
        return text, None, []

    tenant_id = _resolve_tenant_id()
    sid = _resolve_session_id(request_params, session_id)

    parsed: Any = None
    trimmed = text.strip()
    if (trimmed.startswith("{") and trimmed.endswith("}")) or (
        trimmed.startswith("[") and trimmed.endswith("]")
    ):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None

    if isinstance(parsed, (dict, list)):
        slim, refs = await _run_offload_minio(
            parsed,
            tool_name=tool_name,
            session_id=sid,
            tenant_id=tenant_id,
            min_bytes=eff_min,
            max_artifacts=eff_max,
            source=source,
        )
        if refs:
            new_text = json.dumps(slim, default=str)
            structured = slim if isinstance(slim, dict) else None
            return new_text, structured, refs
        # Leaf-walk found nothing; fall through to whole-string wrap/unwrap.

    # Non-JSON, or JSON with no qualifying leaves — same wrap/unwrap as offload_raw_result.
    wrapped, refs = await _run_offload_minio(
        {"data": text},
        tool_name=tool_name,
        session_id=sid,
        tenant_id=tenant_id,
        min_bytes=eff_min,
        max_artifacts=eff_max,
        source=source,
    )
    if refs and isinstance(wrapped, dict):
        return wrapped.get("data", text), None, refs
    return text, None, []


async def offload_raw_result(
    data: Any,
    *,
    tool_name: str = "",
    request_params: dict | None = None,
    session_id: str | None = None,
) -> Any:
    """Offload-only pass for ``raw_response``/text tool results.

    Text/passthrough responses (e.g. CSV export downloads, raw file text)
    skip the shape+format pipeline entirely by design — callers need the
    body byte-identical. They still deserve MinIO offload though: a large
    CSV export is exactly the payload this subsystem exists to catch. Runs
    only the offload step; returns *data* unchanged when it's not a string
    or doesn't qualify. Fail-safe like the rest of the pipeline.
    """
    if not isinstance(data, str):
        return data
    # Delegate to offload_text_body so event emission + real refs share one path.
    new_text, _structured, _refs = await offload_text_body(
        data,
        tool_name=tool_name,
        request_params=request_params,
        session_id=session_id,
    )
    return new_text


def _bake_artifact_meta(meta: dict[str, Any], refs: list[dict[str, Any]]) -> None:
    """Attach public artifact refs + retrieval hint onto a _meta dict."""
    public = _public_offload_refs(refs)
    if not public:
        return
    meta["artifacts"] = public
    meta["artifact_hint"] = ARTIFACT_HINT


def _pipeline_step_value(shape: dict[str, Any], key: str) -> Any:
    """Resolve whether a pipeline step should run (and with what arg)."""
    if key in ("strip_base64", "strip_hex", "normalize", "strip_empty"):
        return shape.get(key, True)
    if key == _OFFLOAD_MINIO_KEY:
        # Explicit false/true wins; else default when MinIO + artifact_offload on.
        if key in shape:
            return shape[key]
        return _minio_offload_default()
    return shape.get(key)


def _merged_static_shape(operation_id: str) -> dict[str, Any]:
    """Merge base + op response_shapes (caller may still override)."""
    return {
        **(_SHAPES.get("base") or {}),
        **(_SHAPES.get(operation_id) or {} if operation_id else {}),
    }


async def apply_static_shape_async(
    data: Any,
    operation_id: str,
    *,
    tool_name: str = "",
    request_params: dict | None = None,
    session_id: str | None = None,
    extra_shape: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Full 11-step async pipeline for base + op shapes (includes offload_minio).

    Defaults ``response_format`` to ``json`` when unset so projection/strip
    keep structure (CSV is for wire paths via endpoint_meta / explicit shape).

    Returns ``(payload, applied_meta)``. When include_meta is not set on the
    merged shape, ``payload`` is the shaped data (post-format). When include_meta
    is true, ``payload`` is the full ``{_meta, data}`` envelope.
    """
    shape = _merged_static_shape(operation_id)
    if extra_shape:
        shape = {**shape, **extra_shape}
    # Static callers usually want the raw shaped body, not discovery mode.
    if not shape:
        shape = {"normalize": True}
    if "response_format" not in shape and "default_response_format" not in shape:
        shape = {**shape, "response_format": "json"}
    want_meta = bool(shape.get("include_meta"))
    # Always include_meta for the run so applied_shape is available to return.
    run_shape = {**shape, "include_meta": True}
    out = await apply_shape_async(
        data,
        run_shape,
        tool_name=tool_name or operation_id or "",
        request_params=request_params,
        session_id=session_id,
    )
    applied: dict[str, Any] = {}
    if isinstance(out, dict) and isinstance(out.get("_meta"), dict):
        applied = dict(out["_meta"].get("applied_shape") or {})
        if want_meta:
            return out, applied
        return out.get("data", out), applied
    return out, applied


_NATIVE_HAS_MORE_KEYS = ("has_more", "hasMore", "has_next", "hasNext", "next_page", "nextPage")
_NATIVE_TOTAL_KEYS = ("total", "totalItems", "totalCount", "count", "recordsTotal")
_NATIVE_CURSOR_KEYS = ("next_cursor", "nextCursor", "cursor", "next_token", "nextToken")
_ITEMS_KEYS = ("items", "rows", "data", "results", "list")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (ValueError, TypeError):
        return default

def _infer_pagination(raw: Any, request_params: dict) -> dict[str, Any] | None:
    """Infer pagination state from any response shape.

    Priority:
    1. Native boolean flag (has_more / hasMore / has_next / hasNext / next_page)
    2. Cursor-based token (next_cursor / nextCursor / next_token)
    3. Exact math when a total count field is present
    4. Saturation heuristic (returned count >= requested page_size → assume more)

    Falls back to None when the response contains no list data or no page_size
    can be determined.
    """
    if type(raw) is not dict:
        # bare list — can only use heuristic, need page_size from request
        items = raw if type(raw) is list else None
        if items is None:
            return None
        count = len(items)
        page_size = _safe_int(request_params.get("pageSize", request_params.get("limit", 0)), 0)
        page_index = _safe_int(request_params.get("pageIndex", request_params.get("page", 1)), 1)
        if page_size == 0:
            return None
        has_more = count >= page_size
        return _build_pagination_result(has_more, page_index, page_size, count, None)

    # Native boolean flag
    for key in _NATIVE_HAS_MORE_KEYS:
        val = raw.get(key)
        if type(val) is bool:
            page_size = _safe_int(request_params.get("pageSize", request_params.get("limit", 0)), 0)
            page_index = _safe_int(request_params.get("pageIndex", request_params.get("page", 1)), 1)
            items = next((raw[k] for k in _ITEMS_KEYS if type(raw.get(k)) is list), None)
            count = len(items) if items is not None else None
            total = next((raw[k] for k in _NATIVE_TOTAL_KEYS if type(raw.get(k)) is int), None)
            return _build_pagination_result(val, page_index, page_size, count, total, native=True)

    # Cursor token
    for key in _NATIVE_CURSOR_KEYS:
        cursor = raw.get(key)
        if cursor is not None and cursor != "" and cursor is not False:
            items = next((raw[k] for k in _ITEMS_KEYS if type(raw.get(k)) is list), None)
            count = len(items) if items is not None else None
            return {
                "has_more": True,
                "cursor": cursor,
                "returned_count": count,
                "fetch_hint": f"More items available. Re-call with next_cursor={cursor!r}.",
            }

    # Detect item list + request pagination param
    items = next((raw[k] for k in _ITEMS_KEYS if type(raw.get(k)) is list), None)
    if items is None:
        return None

    count = len(items)
    page_size = _safe_int(request_params.get("pageSize", request_params.get("limit", 0)), 0)
    page_index = _safe_int(request_params.get("pageIndex", request_params.get("page", 1)), 1)
    if page_size == 0:
        return None

    # Exact math from total field
    total: int | None = next(
        (raw[k] for k in _NATIVE_TOTAL_KEYS if type(raw.get(k)) is int), None
    )
    if total is not None:
        has_more = (page_index * page_size) < total
        return _build_pagination_result(has_more, page_index, page_size, count, total)

    # Saturation heuristic
    has_more = count >= page_size
    return _build_pagination_result(has_more, page_index, page_size, count, None)


def _build_pagination_result(
    has_more: bool,
    page_index: int,
    page_size: int,
    count: int | None,
    total: int | None,
    *,
    native: bool = False,
) -> dict[str, Any]:
    if not has_more:
        result: dict[str, Any] = {"has_more": False, "current_page": page_index}
        if count is not None:
            result["returned_count"] = count
        if total is not None:
            result["total"] = total
        return result

    result = {"has_more": True, "current_page": page_index}
    if page_size:
        result["next_page_index"] = page_index + 1
        result["page_size"] = page_size
    if count is not None:
        result["returned_count"] = count
    if total is not None:
        result["total"] = total
    if native:
        result["source"] = "native_flag"
    hint_parts = [f"pageIndex={page_index + 1}"]
    if page_size:
        hint_parts.append(f"pageSize={page_size}")
    result["fetch_hint"] = f"More items available. Re-call with {', '.join(hint_parts)} to fetch next page."
    return result


def build_meta_envelope(
    raw: Any,
    tool_name: str,
    request_params: dict | None = None,
) -> dict[str, Any]:
    """Wrap *raw* with discovery hints so the LLM knows how to shape the next call."""
    raw_str = json.dumps(raw, default=str)
    size = len(raw_str.encode("utf-8"))

    items = None
    if type(raw) is list:
        items = raw
    elif type(raw) is dict and type(raw.get("items")) is list:
        items = raw["items"]

    meta: dict[str, Any] = {
        "tool": tool_name,
        "raw_size_bytes": size,
        "shaped": False,
    }
    if items is not None:
        meta["item_count"] = len(items)
        if items and type(items[0]) is dict:
            meta["sample_item_keys"] = sorted(items[0].keys())[:20]
    if type(raw) is dict:
        meta["top_level_keys"] = sorted(raw.keys())[:20]

    if size > _LARGE_RESPONSE_THRESHOLD:
        meta["suggestion"] = (
            'Response is large. Re-call with _response_shape={"limit": 20, '
            '"project": {...}, "strip_keys": [...]} to reduce.'
        )

    meta["pagination"] = _infer_pagination(raw, request_params or {})

    return {"_meta": meta, "data": raw}


async def apply_shape_async(
    data: Any,
    shape: dict[str, Any] | None,
    tool_name: str = "",
    request_params: dict | None = None,
    session_id: str | None = None,
) -> Any:
    """Canonical async 11-step response pipeline (all production features).

    Pipeline order when *shape* is provided:
      1. normalize
      2. offload_minio  (default when MinIO available + artifact_offload.enabled)
      3. strip_base64
      4. strip_hex
      5. strip_keys
      6. strip_empty
      7. dedupe
      8. project
      9. limit
     10. include_meta envelope
     11. apply_format_async (CSV etc. — size/token fallback after offload)

    When ``offload_minio`` runs, large string leaves become markers and public
    refs + ``fetch_artifact`` hint are baked into ``_meta``.
    """
    if type(data) is dict and data.get("status") == "error":
        return data

    if shape and shape.get("meta_only"):
        cleaned = _strip_hex(strip_base64_fields(data))
        envelope = build_meta_envelope(cleaned, tool_name, request_params=request_params)
        return {"_meta": envelope["_meta"]}

    if shape is None:
        is_list_payload = isinstance(data, list) or (
            isinstance(data, dict) and any(k in data for k in ("items", "data", "results", "rows") if isinstance(data.get(k), list))
        )
        if is_list_payload:
            from utils.tools_api_config import get_endpoint_meta
            _static = {
                **_SHAPES.get("base", {}),
                **(_SHAPES.get(tool_name, {}) if tool_name else {}),
            }
            _endpoint = get_endpoint_meta(tool_name)
            shape = {**_static, **_endpoint}

    if not shape:
        cleaned = _strip_hex(strip_base64_fields(data))
        return build_meta_envelope(cleaned, tool_name, request_params=request_params)

    if shape.get("passthrough"):
        # Opt-out for structured-dict tools (e.g. a UI layout payload) that
        # must never be strip/project/CSV-reshaped — but still deserve MinIO
        # offload for oversized leaves. See config/tools_api_config.json.
        # Bypasses normalize/strip/dedupe/project/limit/format/envelope
        # entirely; returns the flat (possibly offloaded) payload as-is.
        want_offload = shape["offload_minio"] if "offload_minio" in shape else _minio_offload_default()
        if not want_offload:
            return data
        tenant_id = _resolve_tenant_id()
        sid = _resolve_session_id(request_params, session_id)
        slim, _refs = await _run_offload_minio(
            data, tool_name=tool_name, session_id=sid, tenant_id=tenant_id
        )
        return slim

    currentMeta: dict[str, Any] = {"base": _SHAPES.get("base", {})}
    offload_refs: list[dict[str, Any]] = []
    tenant_id = _resolve_tenant_id()
    sid = _resolve_session_id(request_params, session_id)

    for key, transform_fn in _PIPELINE_MAP.items():
        val = _pipeline_step_value(shape, key)
        if val is None or val is False:
            continue

        if key == _OFFLOAD_MINIO_KEY:
            data, offload_refs = await _run_offload_minio(
                data,
                tool_name=tool_name,
                session_id=sid,
                tenant_id=tenant_id,
            )
            currentMeta[key] = True if offload_refs else bool(val)
            continue

        if transform_fn is None:
            continue

        if key == "normalize":
            data = transform_fn(data, val, shape.get("include_meta", False))
        else:
            data = transform_fn(data, val)
        currentMeta[key] = val

    # Step 10–11: meta envelope + async format
    if shape.get("include_meta", False):
        envelope = build_meta_envelope(data, tool_name, request_params=request_params)
        envelope["_meta"]["shaped"] = True
        envelope["_meta"]["applied_shape"] = currentMeta
        envelope["_meta"]["response_format"] = shape.get(
            "response_format", shape.get("default_response_format", "csv")
        )
        if offload_refs:
            _bake_artifact_meta(envelope["_meta"], offload_refs)
        envelope["data"] = await apply_format_async(data, shape)
        return envelope

    # No include_meta: always return the flat shaped/formatted payload — never
    # wrap in {_meta, data}. Tool-boundary consumers (GOAP arg-binding
    # resolver, validator_node, fold_working_memory, ...) all expect a flat
    # list/dict; only the explicit include_meta=True path above produces an
    # envelope. Offload refs still surface via the inline "[offloaded: ...
    # short_id=...]" marker left in the data by _run_offload_minio, plus the
    # "artifacts.offloaded" EventBus notify already fired there.
    return await apply_format_async(data, shape)



def reload_shapes() -> None:
    """Re-read tools_api_config.json into module globals (useful for tests or hot-reload)."""
    import importlib
    import utils.tools_api_config as _cfg
    importlib.reload(_cfg)
    global _SHAPES, SETTINGS, _BASE64_MIN_LEN
    _SHAPES = _cfg.RESPONSE_SHAPES
    SETTINGS = _cfg.SETTINGS
    _BASE64_MIN_LEN = SETTINGS.get("base64_min_len", 128)
    logger.info(f"Reloaded {len(_SHAPES)} response shapes")
