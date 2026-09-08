"""Defensive API call utilities for Whiskers Agent tools.

Provides a typed ``safe_api_call`` wrapper and helpers that convert HTTP
failures into structured error dicts instead of raising exceptions, so the
LLM always receives a meaningful message rather than a generic 400/500 error.

Usage::

    from .api_utils import safe_api_call

    return safe_api_call(
        lambda: requests.post(url, json=payload, headers=_auth_headers(), timeout=30),
        lambda resp: resp.json(),
        context=f"Creating record {given_name} {last_name}",
        record_name=f"{given_name} {last_name}",
    )
"""

from __future__ import annotations
import asyncio
import datetime
import json
import logging
from typing import Any, Callable, TypeVar, Awaitable

import requests

from utils.response_shape import apply_shape_async, offload_raw_result
from utils.tools_api_config import get_endpoint_meta, RESPONSE_SHAPES
from utils.json_processor import extract_by_keys
from utils.server_config import SAFE_DEFAULT_PAGE_SIZE, SAFE_DEFAULT_PAGE_INDEX

logger = logging.getLogger("whiskers")

# Type parameter for the success return value of safe_api_call.
T = TypeVar("T")

# Fields checked in order when parsing an API error body.
_ERROR_FIELDS = ("message", "error", "errors", "detail", "msg")


# Factory functions for safe default values based on JSON Schema types
_SCHEMA_DEFAULT_FACTORIES: dict[str, Callable[[], Any]] = {
    "object": dict,
    "array": list,
    "boolean": lambda: False,
}

def _apply_schema_defaults(
    params: dict[str, Any],
    schema_props: dict[str, Any] | None,
) -> None:
    """Fill in safe defaults for omitted or None-valued fields declared in schema_props."""
    if not schema_props:
        return
    for k, prop in schema_props.items():
        if k not in params or params.get(k) is None:
            prop_type = prop.get("type", "string")
            if prop_type in _SCHEMA_DEFAULT_FACTORIES:
                params[k] = _SCHEMA_DEFAULT_FACTORIES[prop_type]()


def _primary_type(prop: dict[str, Any]) -> str | None:
    """Return the primary (non-null) JSON Schema type for a property."""
    if "type" in prop:
        return prop["type"]
    for sub in prop.get("anyOf", []):
        t = sub.get("type")
        if t and t != "null":
            return t
    return None


def _infer_query_default(prop: dict[str, Any]) -> str | None:
    """Return a safe query-string fallback for an optional field that was not provided.

    Rules (applied in order):
    1. Description contains "json-encoded array"  → ``"[]"``
    2. Description contains any "json-encoded"    → ``"{}"``
       (covers "json-encoded object", "json-encoded filter object",
        "json-encoded value", "json-encoded boolean", etc.)
    3. Primary type is ``boolean``                → ``"false"``
    4. anyOf contains ``boolean``                 → ``"false"``
       (handles anyOf[boolean, null] nullable booleans)
    5. Anything else (plain optional strings, integers, objects, arrays)
       → ``None`` (omit the field entirely)

    Rule 5 deliberately skips anyOf[string, null] fields that have NO "json-encoded"
    description (e.g. keywordSearch, searchString, token).  Sending a literal
    ``"null"`` for those would make ``if (req.query.keyword)`` truthy in Node.js
    and pass the string "null" to the backend — incorrect behaviour.
    Only fields that are explicitly JSON-encoded need a non-empty fallback.
    """
    description = prop.get("description", "").lower()
    if "json-encoded array" in description:
        return "[]"
    if "json-encoded" in description:
        return "{}"

    ptype = _primary_type(prop)
    if ptype == "boolean":
        return "false"

    any_of_types = {sub.get("type") for sub in prop.get("anyOf", []) if sub.get("type")}
    if "boolean" in any_of_types:
        return "false"

    return None


_PARAM_FORMATTERS: dict[type, Callable[[Any], str]] = {
    bool: lambda v: "true" if v else "false",
    dict: json.dumps,
    list: json.dumps,
    datetime.datetime: lambda v: v.isoformat().replace("+00:00", "Z"),
    datetime.date: lambda v: v.isoformat(),
}


def _serialize_datetimes(val: Any) -> Any:
    """Recursively convert datetime.datetime and datetime.date objects to ISO-8601 strings."""
    if isinstance(val, (datetime.datetime, datetime.date)):
        s = val.isoformat()
        if s.endswith("+00:00"):
            s = s[:-6] + "Z"
        return s
    elif isinstance(val, dict):
        return {k: _serialize_datetimes(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_serialize_datetimes(item) for item in val]
    elif isinstance(val, tuple):
        return tuple(_serialize_datetimes(item) for item in val)
    return val

def sanitize_params(
    params: dict[str, Any] | None,
    schema_props: dict[str, Any] | None = None,
    required_fields: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Sanitize **query-string** parameters before sending to an upstream API.

    * Removes ``None`` / ``"undefined"`` values.
    * Coerces booleans to ``"true"``/``"false"`` strings (URL-safe).
    * JSON-encodes ``dict``/``list`` values (prevents Node.js parse errors).
    * Injects safe defaults for omitted boolean and explicitly JSON-encoded
      optional fields, ensuring backends that call ``JSON.parse()``
      unconditionally never receive ``undefined``.

    Args:
        params:          The raw keyword arguments after path params are removed.
        schema_props:    ``inputSchema.properties`` dict from the tool definition.
        required_fields: Set of required field names; defaults are never injected
                         for these (they should already be present or have failed
                         required-field validation).

    Do **not** use this for JSON request bodies — use :func:`sanitize_body`
    instead to preserve native types.
    """
    if not params:
        params = {}

    _apply_schema_defaults(params, schema_props)
    params = _serialize_datetimes(params)

    cleaned: dict[str, Any] = {}
    for k, v in params.items():
        if v is None or v == "undefined":
            continue
        formatter = _PARAM_FORMATTERS.get(type(v), str)
        cleaned[k] = formatter(v)

    # Inject safe defaults for every optional field absent from the request.
    # This prevents Node.js backends from receiving undefined when they call
    # JSON.parse() on query params without a null-check.
    if schema_props:
        for k, prop in schema_props.items():
            if k in cleaned:
                continue
            if required_fields and k in required_fields:
                continue
            default_val = _infer_query_default(prop)
            if default_val is not None:
                cleaned[k] = default_val

    return cleaned


def sanitize_body(
    params: dict[str, Any] | None,
    schema_props: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sanitize a **JSON request body** before sending to an upstream API.

    * Removes ``None`` / ``"undefined"`` values.
    * Preserves native Python types (``bool``, ``dict``, ``list``, ``int``, …)
      so they serialise correctly via ``requests``' ``json=`` parameter.
    * Applies schema defaults when *schema_props* is provided.

    Use :func:`sanitize_params` for query-string parameters instead.
    """
    if not params:
        params = {}

    _apply_schema_defaults(params, schema_props)
    params = _serialize_datetimes(params)

    return {k: v for k, v in params.items() if v is not None and v != "undefined"}



def inject_pagination_defaults(kw: dict, endpoint_meta: dict) -> dict:
    """For paginated list endpoints, inject safe page defaults when the caller
    omitted pagination params. Prevents Whiskers Agent from returning unbounded result
    sets (e.g. 1,000+ records) that blow the LLM context window.
    """
    if not endpoint_meta.get("paginated", False):
        return kw

    if any(k in kw for k in ("pageSize", "pageIndex", "limit", "offset", "page")):
        return kw  # caller already specified pagination — respect it

    default_size = endpoint_meta.get("default_page_size", SAFE_DEFAULT_PAGE_SIZE)
    return {**kw, "pageSize": default_size, "pageIndex": SAFE_DEFAULT_PAGE_INDEX}


def _extract_api_error(resp: requests.Response, operation_id: str | None = None) -> str:
    """Extract a human-readable error message from a failed HTTP response.

    Tries to parse the response body as JSON and look for common error fields.
    Falls back to raw response text (truncated) or the HTTP status code.

    Args:
        resp: The failed :class:`requests.Response`.
        operation_id: The ID of the operation being executed.

    Returns:
        A human-readable error string.
    """
    try:
        body = resp.json()
        if isinstance(body, dict):
            msg = extract_by_keys(body, _ERROR_FIELDS)
            if msg:
                return msg
            if not body:
                method = resp.request.method if resp.request else "UNKNOWN"
                url = resp.request.url if resp.request else "UNKNOWN"
                logger.warning("Empty JSON response body for %s %s (operation_id=%s)", method, url, operation_id)
        elif isinstance(body, str) and body:
            return body
    except Exception:
        logger.debug("api_utils.py: swallowed exception", exc_info=True)

    if resp.text and resp.text.strip():
        text_stripped = resp.text.strip()
        if text_stripped != "{}":
            return text_stripped[:300]

    method = resp.request.method if resp.request else "UNKNOWN"
    url = resp.request.url if resp.request else "UNKNOWN"
    reason = resp.reason or "Unknown"
    return f"HTTP {resp.status_code} ({reason}) on {method} {url}"


async def _apply_response_shape(
    result: Any,
    operation_id: str | None,
    tool_name: str,
    request_params: dict[str, Any] | None,
    shape: dict[str, Any] | None,
    session_id: str | None = None,
) -> Any:
    """Extract the response shaping logic from safe_api_call.

    Uses :func:`apply_shape_async` so ``offload_minio`` can capture large leaves
    before strip/CSV (envelope sanitize remains the size fallback).
    """
    # shape=None  → meta-discovery mode (wraps in _meta envelope with pagination hints).
    # shape=dict  → full pipeline (offload → strip/dedupe/project/limit/format).
    # Merge: base static → op-specific static → endpoint meta → caller shape (caller wins)
    _static: dict[str, Any] = {
        **RESPONSE_SHAPES.get("base", {}),
        **(RESPONSE_SHAPES.get(operation_id, {}) if operation_id else {}),
    }
    _endpoint = get_endpoint_meta(tool_name)

    if shape is None or shape == {}:
        shape = {**_static, **_endpoint}
    elif shape is not None:
        shape = {**_static, **_endpoint, **shape}

    return await apply_shape_async(
        result,
        shape,
        tool_name=tool_name,
        request_params=request_params,
        session_id=session_id,
    )


# Public alias for proxy / non-safe_api_call callers (underscore name kept for back-compat).
apply_response_shape = _apply_response_shape
"""Public alias for proxy and non-safe_api_call callers to apply response shaping (underscore name kept for back-compat)."""


def _infer_plugin_id() -> str | None:
    """Infer the registered plugin_id from the calling stack.

    Walks frames for modules under ``plugins.<package>…``. Prefers
    ``PLUGIN_ID`` / Plugin ``name`` from that package's ``plugin_config``
    (manifest ``name`` if present, otherwise the package directory name).
    Falls back to the package directory name. No hardcoded plugin list.
    """
    import importlib
    import inspect

    for frame_info in inspect.stack():
        module = inspect.getmodule(frame_info[0])
        if not module or not module.__name__:
            continue
        name = module.__name__
        if not name.startswith("plugins."):
            continue
        parts = name.split(".")
        if len(parts) < 2:
            continue
        package = parts[1]
        if not package or package.startswith("_"):
            continue
        try:
            cfg = importlib.import_module(f"plugins.{package}.plugin_config")
            pid = getattr(cfg, "PLUGIN_ID", None)
            if isinstance(pid, str) and pid.strip():
                return pid.strip()
            # Local Plugin subclass ``name`` (skip imported base Plugin).
            pkg_prefix = f"plugins.{package}"
            for attr in dir(cfg):
                obj = getattr(cfg, attr, None)
                if not (isinstance(obj, type) and attr.endswith("Plugin")):
                    continue
                if not str(getattr(obj, "__module__", "")).startswith(pkg_prefix):
                    continue
                n = getattr(obj, "name", None)
                if isinstance(n, str) and n.strip():
                    return n.strip()
        except Exception:
            logger.debug("api_utils.py: swallowed exception", exc_info=True)
        return package
    return None


def _attach_error_meta(exc: Exception, error: dict[str, Any]) -> Exception:
    """Stamp structured error fields (http_status, error type) onto an exception.

    ``raise_tool_error=True`` callers need a real exception for MCP protocol
    correctness, but the graph executor's fast-path branch only sees a
    stringified message after catching it — without these attributes,
    ``is_recoverable()`` can't tell a 400/422 (replannable) apart from a 5xx,
    and the validator halts instead of replanning.
    """
    try:
        exc.http_status = error.get("http_status")
        exc.api_error_type = error.get("error")
    except Exception:
        logger.debug("api_utils.py: swallowed exception", exc_info=True)
    return exc


def _handle_api_exception(
    exc: Exception,
    resp: requests.Response | None,
    context: str = "",
    plugin_id: str | None = None,
    raise_tool_error: bool = True,
    **extra_error_fields: Any,
) -> dict[str, Any]:
    """Extract exception handling logic from safe_api_call."""
    from fastmcp.exceptions import ToolError

    if isinstance(exc, requests.exceptions.HTTPError):
        if resp is not None:
            if resp.status_code == 401:
                inferred_plugin_id = plugin_id or _infer_plugin_id()
                if inferred_plugin_id:
                    try:
                        from core.plugin_loader.plugin_registry import get_registry
                        get_registry().auth.mark_needs_reauth(inferred_plugin_id)
                    except Exception as mark_exc:
                        logger.warning("safe_api_call: failed to mark plugin as needs_reauth: %s", mark_exc)
            error = api_error_dict(resp, context, **extra_error_fields)
        else:
            error = {
                "status": "error",
                "error": "api_error",
                "message": context or "HTTP error occurred",
                **extra_error_fields,
            }
        logger.error(f"✗ {error['message']}")
        if raise_tool_error:
            if resp is not None and resp.status_code >= 500:
                raise _attach_error_meta(exc, error)
            raise _attach_error_meta(ToolError(error["message"]), error)
        return error
    elif isinstance(exc, requests.exceptions.RequestException):
        error = {
            "status": "error",
            "error": "request_failed",
            "message": f"{context}: {exc}" if context else str(exc),
            "status_code": 500,
            "raw_body": str(exc),
            **extra_error_fields,
        }
        logger.error(f"✗ {error['message']}")
        if raise_tool_error:
            raise _attach_error_meta(ToolError(error["message"]), error)
        return error
    else:
        error = {
            "status": "error",
            "error": "pipeline_error",
            "message": f"{context}: unexpected error — {exc}" if context else str(exc),
            "status_code": 500,
            "raw_body": str(exc),
            **extra_error_fields,
        }
        logger.error(f"✗ {error['message']}", exc_info=True)
        if raise_tool_error:
            raise _attach_error_meta(exc, error)
        return error


def api_error_dict(
    resp: requests.Response,
    context: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """Build a standard error response dict from a failed HTTP response.

    Args:
        resp: The failed :class:`requests.Response`.
        context: Human-readable description of the operation (prepended to message).
        **extra: Additional key/value pairs merged into the returned error dict.

    Returns:
        A dict with at minimum: ``status``, ``error``, ``http_status``, ``message``.
    """
    msg = _extract_api_error(resp, operation_id=extra.get("operation_id"))
    _raw = resp.text
    result: dict[str, Any] = {
        "status": "error",
        "error": "api_error",
        "http_status": resp.status_code,
        "status_code": resp.status_code,
        "raw_body": _raw[:2000] + ("…" if len(_raw) > 2000 else ""),
        "message": f"{context}: {msg}" if context else msg,
    }
    if extra:
        result.update(extra)
    return result


async def _exec_request(fn: Callable[[], Any]) -> Any:
    import inspect
    if inspect.iscoroutinefunction(fn):
        return await fn()
    res = await asyncio.to_thread(fn)
    if inspect.isawaitable(res):
        return await res
    return res


async def safe_api_call(
    make_request: Callable[[], Any],
    on_success: Callable[[Any], T],
    context: str = "",
    operation_id: str | None = None,
    shape: dict[str, Any] | None = None,
    tool_name: str = "",
    request_params: dict[str, Any] | None = None,
    raw_response: bool = False,
    auth_retry: Callable[[], Awaitable[dict[str, str]]] | None = None,
    headers: dict[str, str] | None = None,
    plugin_id: str | None = None,
    raise_tool_error: bool = True,
    **extra_error_fields: Any,
) -> T | dict[str, Any]:
    """Execute an HTTP call defensively, returning a structured error dict or raising ToolError."""
    import time
    t0 = time.perf_counter()
    resp: Any = None
    try:
        resp = await _exec_request(make_request)
        _log_body = "" if raw_response else resp.text[:500]
        logger.debug(f"Received response (status={resp.status_code}): {_log_body}")

        # Check for 401 Unauthorized and try a one-shot retry
        if resp.status_code == 401:
            actual_auth_retry = auth_retry
            if actual_auth_retry is None and plugin_id:
                try:
                    from core.plugin_loader.plugin_registry import get_registry
                    actual_auth_retry = lambda: get_registry().auth.refresh_auth_headers(plugin_id)
                except Exception as fallback_exc:
                    logger.warning("safe_api_call: failed to build fallback auth_retry: %s", fallback_exc)

            if actual_auth_retry is not None and headers is not None:
                logger.info("safe_api_call: 401 Unauthorized encountered. Attempting auth_retry...")
                try:
                    new_headers = await _exec_request(actual_auth_retry)
                    headers.clear()
                    headers.update(new_headers)
                    logger.info("safe_api_call: Updated headers reference. Retrying request...")
                    resp = await _exec_request(make_request)
                    _log_body = "" if raw_response else resp.text[:500]
                    logger.debug(f"Received retry response (status={resp.status_code}): {_log_body}")
                except Exception as retry_exc:
                    logger.error("safe_api_call: auth_retry or retry request failed: %s", retry_exc)

        resp.raise_for_status()
        logger.info(f"✓ API call successful: {context}")
        result = on_success(resp)
        logger.debug(f"Processed API result: {result}")

        # Record successful telemetry
        latency_ms = int((time.perf_counter() - t0) * 1000)
        try:
            from core.telemetry import collector
            collector.record_tool_call(
                tool=tool_name or operation_id or "unknown",
                plugin_id=plugin_id or "unknown",
                model=None,
                latency_ms=latency_ms,
                ok=True
            )
        except Exception as e:
            logger.debug("Failed to record telemetry: %s", e)

        if raw_response:
            # Text/passthrough responses skip strip/project/CSV by design
            # (callers need the body byte-identical), but a large CSV export
            # or file-text response still deserves MinIO offload.
            return await offload_raw_result(
                result, tool_name=tool_name or operation_id or "", request_params=request_params
            )

        return await _apply_response_shape(
            result, operation_id, tool_name, request_params, shape
        )
    except Exception as exc:
        # Record failed telemetry
        latency_ms = int((time.perf_counter() - t0) * 1000)
        status_code = resp.status_code if resp is not None else None
        error_type = type(exc).__name__
        try:
            from core.telemetry import collector
            collector.record_tool_call(
                tool=tool_name or operation_id or "unknown",
                plugin_id=plugin_id or "unknown",
                model=None,
                latency_ms=latency_ms,
                ok=False,
                error_type=error_type,
                status_code=status_code
            )
        except Exception as e:
            logger.debug("Failed to record telemetry: %s", e)

        return _handle_api_exception(
            exc,
            resp,
            context,
            plugin_id=plugin_id,
            raise_tool_error=raise_tool_error,
            operation_id=operation_id,
            **extra_error_fields,
        )

