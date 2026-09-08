"""
core/dynamic_tools/loader.py — Data-driven MCP tool registration.

Reads every ``mcp-tools.json`` file in a plugin's ``mcp-tools-context/`` folder
tree and registers each tool definition as a live ``@mcp.tool()`` using the
existing FastMCP singleton.  **No Python code needed per-tool** — adding (or
removing) a JSON entry is enough to expose that plugin API endpoint over MCP.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import requests

from core.context import mcp
from core.proxy.ssrf_safety import _is_safe_url
from core.proxy.proxy_manager import _safe_async_client
from utils.api_utils import (
    safe_api_call,
    sanitize_body,
    sanitize_params,
    inject_pagination_defaults,
)
from utils.json_processor import safe_json_response, safe_text_response
from utils.server_config import (
    MAX_RECOMMENDED_PAGE_SIZE,
    SAFE_DEFAULT_PAGE_SIZE,
    SAFE_DEFAULT_PAGE_INDEX,
)
from utils.tools_api_config import get_endpoint_meta

logger = logging.getLogger("whiskers")

# Module-level mapping that persists across load/unload cycles.
# Keyed by plugin_id -> set of registered tool names.
_registered_names: dict[str, set[str]] = {}

# ---------------------------------------------------------------------------
# JSON Schema type → Python type
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, type] = {
    "integer": int,
    "string": str,
    "boolean": bool,
    "number": float,
    "object": dict,
    "array": list,
}


def _should_inject_shape(meta: dict, op_name: str) -> bool:
    """Explicit meta flag wins; otherwise default to injecting on read ops only."""
    if "shapeable" in meta:
        return bool(meta["shapeable"])
    return op_name == "read"


def _json_type(schema_type: str) -> type:
    return _TYPE_MAP.get(schema_type, str)


def _prop_py_type(prop: dict) -> type:
    """Resolve the Python type for a single property dict from an inputSchema.

    Handles:
    - Plain ``{"type": "X"}``
    - Pydantic v2 ``{"$ref": "..."}`` (nested model → dict)
    - Pydantic v2 ``{"anyOf": [...]}`` nullable types, e.g. ``anyOf[{$ref}, {type:null}]``
    - anyOf unions with a plain type, e.g. ``anyOf[{type:"string"},{type:"null"}]``
    """
    # Direct type declaration
    if "type" in prop:
        return _json_type(prop["type"])

    # $ref → it's a nested model schema → use dict
    if "$ref" in prop:
        return dict

    # anyOf handling (pydantic v2 nullables and nested model refs)
    any_of = prop.get("anyOf", [])
    if any_of:
        for sub in any_of:
            if "$ref" in sub:
                return dict  # nested object model
        for sub in any_of:
            t = sub.get("type")
            if t and t != "null":
                return _json_type(t)

    return str


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

# Matches both OpenAPI-style {param} and Express-style :param
_PATH_PARAM_RE = re.compile(r"\{(\w+)\}|:(\w+)")


def _extract_path_params(path_template: str) -> frozenset[str]:
    return frozenset(
        name for m in _PATH_PARAM_RE.finditer(path_template)
        for name in (m.group(1) or m.group(2),)
    )


def _substitute_path(path_template: str, kw: dict) -> str:
    """Replace ``{param}`` or ``:param`` tokens in *path_template* using *kw*, mutating *kw*."""
    url = path_template
    for m in _PATH_PARAM_RE.finditer(path_template):
        param = m.group(1) or m.group(2)
        if param in kw:
            url = url.replace(m.group(0), str(kw.pop(param)))
    return url


# ---------------------------------------------------------------------------
# Dynamic function factory
# ---------------------------------------------------------------------------

def _make_tool_fn(
    tool_def: dict,
    api_url: str,
    auth_fn: Any,
    op_name: str = "read",
    endpoint_meta: dict | None = None,
    shape_hint: str = "",
    format_hint: str = "",
):  # noqa: ANN201
    """Return a typed async handler for *tool_def*, ready for ``mcp.add_tool``."""

    name: str = tool_def["name"]
    description: str = tool_def.get("description", "")
    meta: dict = tool_def.get("_meta", {})
    method: str = meta.get("method", "GET").upper()
    path_template: str = meta.get("path", "")
    _ep_meta: dict = endpoint_meta or {}

    input_schema: dict = tool_def.get("inputSchema", {})
    schema_props: dict = input_schema.get("properties", {})
    required_fields: frozenset[str] = frozenset(input_schema.get("required", []))
    path_params: frozenset[str] = _extract_path_params(path_template)

    # ── Warn if any path param is not declared as required in the schema ──────
    non_required_path_params = path_params - required_fields
    if non_required_path_params:
        logger.warning(
            "dynamic_tools_loader: tool '%s' has path params not marked as "
            "required in inputSchema: %s. They will be validated at runtime.",
            name,
            sorted(non_required_path_params),
        )

    # Build ordered parameter list (required before optional)
    all_params = list(schema_props.keys())
    ordered_params = sorted(
        all_params,
        key=lambda p: (0 if p in required_fields else 1, p),
    )

    sig_params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}

    for param_name in ordered_params:
        prop = schema_props[param_name]
        py_type = _prop_py_type(prop)
        is_required = param_name in required_fields

        if is_required:
            sig_params.append(
                inspect.Parameter(
                    param_name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=py_type,
                )
            )
            annotations[param_name] = py_type
        else:
            sig_params.append(
                inspect.Parameter(
                    param_name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=None,
                    annotation=Optional[py_type],
                )
            )
            annotations[param_name] = Optional[py_type]

    # Inject _response_shape for shapeable (read) tools
    inject_shape = _should_inject_shape(meta, op_name)
    if inject_shape:
        sig_params.append(
            inspect.Parameter(
                "_response_shape",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=None,
                annotation=Optional[dict],
            )
        )
        annotations["_response_shape"] = Optional[dict]

    # Build description with conditional hints (only append shaping hints for shapeable tools)
    full_description = description
    
    full_description += f"\n\n### Input Schema (Pay attention to nested object structures):\n```json\n{json.dumps(input_schema, indent=2)}\n```\n"

    if inject_shape:
        full_description += shape_hint + format_hint
    if _ep_meta.get("paginated", False):
        _default_ps = _ep_meta.get("default_page_size", SAFE_DEFAULT_PAGE_SIZE)
        full_description += (
            f"\n\nDEFAULT BEHAVIOUR: If pageSize is not provided, this tool defaults to "
            f"pageSize={_default_ps}, pageIndex={SAFE_DEFAULT_PAGE_INDEX} to protect context window budget. "
            f"Check _meta.pagination.has_more to fetch subsequent pages. "
            f"Pass pageSize=N explicitly to override (max recommended: {MAX_RECOMMENDED_PAGE_SIZE})."
        )

    async def _handler(**kwargs: Any) -> Any:
        # Separate shape metadata from API params (never forwarded to downstream APIs)
        shape: dict | None = kwargs.pop("_response_shape", None)
        kw: dict = {k: v for k, v in kwargs.items() if v is not None}

        # Try to parse any JSON-encoded strings passed to expected object/array fields
        for k, v in list(kw.items()):
            if isinstance(v, str) and (v.strip().startswith("{") or v.strip().startswith("[")):
                try:
                    kw[k] = json.loads(v)
                except (json.JSONDecodeError, ValueError) as exc:
                    # Prefer json.loads only — Python AST evaluators can RecursionError on
                    # deeply nested untrusted structures (DoS). Invalid JSON stays as str.
                    logger.debug("loader.py _handler: could not parse field %s for %s: %s", k, name, exc)
        logger.debug("loader.py _handler: name=%s, kw=%s", name, kw)

        # Substitute path params (removes them from kw)
        url: str = _substitute_path(path_template, kw)

        # Retrieve any fields explicitly marked as query parameters
        query_fields: list[str] = meta.get("query", [])

        # Guard: any remaining :param tokens mean the caller omitted a required
        # path parameter that wasn't caught by the schema (or was wrongly optional).
        unresolved = [m.group(1) or m.group(2) for m in _PATH_PARAM_RE.finditer(url)]
        if unresolved:
            return {
                "status": "error",
                "error": "missing_path_params",
                "missing_fields": unresolved,
                "message": (
                    f"Required path parameter(s) not provided: "
                    f"{', '.join(unresolved)}. "
                    f"The URL template is: {path_template}"
                ),
            }

        full_url: str = f"{api_url}{url}"

        # Reject URLs that resolve to private/loopback addresses (SSRF guard)
        if not await _is_safe_url(full_url):
            logger.warning("loader.py _handler: SSRF check rejected URL %s for tool %s", full_url, name)
            return {
                "status": "error",
                "error": "ssrf_rejected",
                "message": f"URL '{full_url}' was rejected by SSRF safety check.",
            }

        # Inject safe pagination defaults before hitting downstream APIs
        kw = inject_pagination_defaults(kw, _ep_meta)

        # Merge response-shaping defaults from endpoint_meta when caller omitted them.
        if shape is not None:
            merged_shape = dict(shape)

            if "response_format" not in merged_shape:
                if _dfmt := _ep_meta.get("default_response_format"):
                    merged_shape["response_format"] = _dfmt

            if "include_meta" not in merged_shape and "include_meta" in _ep_meta:
                merged_shape["include_meta"] = bool(_ep_meta.get("include_meta"))

            shape = merged_shape

        # Resolve auth headers before entering lambda (avoid coroutine capture)
        headers: dict = await auth_fn()

        from core.plugin_loader.plugin_auth_registry import is_auth_error
        if is_auth_error(headers):
            # get_auth_headers() degrades to this dict instead of raising — it must
            # never be forwarded as real HTTP headers to the downstream request below.
            logger.warning(
                "loader.py _handler: auth resolution failed for tool %s: %s", name, headers
            )
            return {
                "status": "error",
                "error": "auth_required",
                "message": f"Auth resolution failed for {name}: {headers.get('error')}",
            }

        # Capture locals for the lambda
        _method = method
        _url = full_url
        _headers = headers

        _operation_id: str = meta.get("operationId", "")
        response_type: str = meta.get("response_type", "json")

        _on_success_map: dict[str, Any] = {
            "text": safe_text_response,
            "json": safe_json_response,
        }
        _on_success = _on_success_map.get(response_type, safe_json_response)

        # text responses must bypass the shaping pipeline entirely
        _raw_response_map: dict[str, bool] = {
            "text": True,
            "json": False,
        }
        _raw_response = _raw_response_map.get(response_type, False)
        _shape_applicable = inject_shape and not _raw_response

        if _method == "GET":
            _query = sanitize_params(kw, schema_props, required_fields)
            async def _make_get_req():
                async with _safe_async_client(timeout=30.0) as client:
                    return await client.get(_url, params=_query, headers=_headers)

            return await safe_api_call(
                _make_get_req,
                _on_success,
                context=name,
                operation_id=_operation_id,
                shape=shape if _shape_applicable else None,
                tool_name=name if not _raw_response else "",
                request_params=_query if _shape_applicable else None,
                raw_response=_raw_response,
            )
        else:
            # Separate query from body parameters using meta["query"]
            _query = {k: v for k, v in kw.items() if k in query_fields}
            _body_kw = {k: v for k, v in kw.items() if k not in query_fields}
            _body = sanitize_body(_body_kw, schema_props)
            logger.info("Jules API Call: method=%s, url=%s, body=%s", _method, _url, _body)
            async def _make_custom_req():
                async with _safe_async_client(timeout=30.0) as client:
                    return await client.request(_method, _url, params=_query, json=_body, headers=_headers)

            return await safe_api_call(
                _make_custom_req,
                _on_success,
                context=name,
                operation_id=_operation_id,
                shape=shape if _shape_applicable else None,
                tool_name=name if not _raw_response else "",
                request_params={**_query, **_body} if _shape_applicable else None,
                raw_response=_raw_response,
            )

    # ── Patch function metadata so FastMCP sees the correct typed signature ───
    _handler.__name__ = name
    _handler.__qualname__ = name
    _handler.__doc__ = full_description
    _handler.__annotations__ = annotations
    _handler.__signature__ = inspect.Signature(parameters=sig_params)

    return _handler


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_tools(
    plugin_id: str,
    tools_dir: Path,
    api_url: str,
    auth_fn: Any,
    domains: list[str] | None = None,
    ops: list[str] | None = None,
    valid_ops: set[str] | None = None,
    tool_prefix: str = "",
    shape_hint: str = "",
    format_hint: str = "",
) -> int:
    """Walk *tools_dir*, register every matching tool, return count registered."""
    tools_dir = Path(tools_dir)
    if not tools_dir.exists():
        logger.warning(
            f"dynamic_tools_loader: mcp-tools-context folder not found at {tools_dir}"
        )
        return 0

    if valid_ops is None:
        valid_ops = {"read", "write_update", "delete"}

    op_filter: set[str] = (
        {o for o in ops if o in valid_ops} if ops else valid_ops
    )

    domain_dirs = sorted(
        d for d in tools_dir.iterdir()
        if d.is_dir() and (domains is None or d.name in domains)
    )

    count = 0
    skipped = 0
    # Use a local set for collision-detection within this run
    seen_this_run: set[str] = set()

    plugin_registered = _registered_names.setdefault(plugin_id, set())

    for domain_dir in domain_dirs:
        for op_dir in sorted(domain_dir.iterdir()):
            if not op_dir.is_dir() or op_dir.name not in op_filter:
                continue

            json_file = op_dir / "mcp-tools.json"
            if not json_file.exists():
                continue

            try:
                tool_defs: list[dict] = json.loads(
                    json_file.read_text(encoding="utf-8")
                )
            except Exception as exc:
                logger.error(f"dynamic_tools_loader: failed to parse {json_file}: {exc}")
                continue

            for tool_def in tool_defs:
                tool_name = tool_def.get("name", "").strip()
                if not tool_name:
                    continue
                original_name = tool_name
                if tool_name in seen_this_run:
                    suffix = 1
                    while tool_name in seen_this_run:
                        tool_name = f"{original_name}_{suffix:02d}"
                        suffix += 1
                    logger.warning(
                        "dynamic_tools_loader: duplicate tool name '%s' in %s — registered as '%s'",
                        original_name, json_file, tool_name,
                    )
                registered_name = f"{tool_prefix}{tool_name}"
                # Skip if already registered in a previous load cycle
                if registered_name in plugin_registered:
                    seen_this_run.add(registered_name)
                    continue
                try:
                    fn = _make_tool_fn(
                        tool_def,
                        api_url=api_url,
                        auth_fn=auth_fn,
                        op_name=op_dir.name,
                        endpoint_meta=get_endpoint_meta(original_name),
                        shape_hint=shape_hint,
                        format_hint=format_hint,
                    )
                    _is_read = op_dir.name == "read"
                    mcp.tool(
                        fn,
                        name=registered_name,
                        title=domain_dir.name,
                        description=fn.__doc__ or "",
                        tags={domain_dir.name, op_dir.name, plugin_id},
                        annotations={
                            "readOnlyHint": _is_read,
                            "idempotentHint": _is_read,
                        },
                    )
                    seen_this_run.add(registered_name)
                    plugin_registered.add(registered_name)
                    count += 1
                except Exception as exc:
                    logger.warning(f"dynamic_tools_loader: skipped '{tool_name}' — {exc}")
                    skipped += 1

    if tool_prefix:
        logger.info("dynamic_tools_loader: using tool prefix '%s'", tool_prefix)
        
    logger.info(
        f"dynamic_tools_loader: registered {count} tools for plugin {plugin_id}"
        + (f", skipped {skipped}" if skipped else "")
        + f" from {tools_dir}"
    )
    return count


def _parse_env_list(env_val: str) -> list[str] | None:
    items = [s.strip() for s in env_val.split(",") if s.strip()]
    return items or None


def load_tools_from_env(
    plugin_id: str,
    tools_dir: Path,
    api_url: str,
    auth_fn: Any,
    valid_ops: set[str] | None = None,
    tool_prefix: str = "",
    shape_hint: str = "",
    format_hint: str = "",
) -> int:
    """Read WHISKERS_TOOL_DOMAINS / WHISKERS_TOOL_OPS env vars and call load_tools()."""
    domains = _parse_env_list(os.environ.get("WHISKERS_TOOL_DOMAINS", ""))
    ops = _parse_env_list(os.environ.get("WHISKERS_TOOL_OPS", ""))
    return load_tools(
        plugin_id=plugin_id,
        tools_dir=tools_dir,
        api_url=api_url,
        auth_fn=auth_fn,
        domains=domains,
        ops=ops,
        valid_ops=valid_ops,
        tool_prefix=tool_prefix,
        shape_hint=shape_hint,
        format_hint=format_hint,
    )


def unload_tools(plugin_id: str) -> int:
    """Remove all tools registered by this loader for the given plugin from FastMCP. Returns count removed."""
    removed = 0
    plugin_registered = _registered_names.get(plugin_id, set())
    for name in list(plugin_registered):
        try:
            mcp.local_provider.remove_tool(name)
            removed += 1
        except Exception as exc:
            logger.warning("unload_tools: could not remove '%s': %s", name, exc)
    plugin_registered.clear()
    logger.info("%s: unloaded %d tools", plugin_id, removed)
    return removed


def get_registered_count(plugin_id: str | None = None) -> int:
    """Return the number of tools currently registered by this loader."""
    if plugin_id is not None:
        return len(_registered_names.get(plugin_id, set()))
    return sum(len(names) for names in _registered_names.values())
