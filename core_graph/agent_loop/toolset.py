"""Resolve ToolRef globs against the live OperationCatalog into LC tool schemas.

Proxy-detection / prefer-glob helpers here are the canonical versions —
``plugins/portfolio_plugin/discovery/resolver.py`` imports ``is_proxy_op`` /
``prefer_match`` from this module instead of duplicating them.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass
from typing import Any

from core_graph.agent_loop.spec import ToolRef

logger = logging.getLogger("whiskers.core_graph.agent_loop")


def tags_of(op: Any) -> set[str]:
    """Extracts and normalizes lowercase tags from a catalog operation object."""
    tags = getattr(op, "tags", ()) or ()
    return {str(t).lower() for t in tags}


def is_proxy_op(op: Any) -> bool:
    """True when the catalog op is a mounted MCP proxy tool."""
    pid = str(getattr(op, "plugin_id", "") or "")
    if pid.startswith("proxy_") or pid.startswith("proxy"):
        return True
    tags = tags_of(op)
    return "proxy" in tags or any(t.startswith("proxy") for t in tags)


def prefer_match(plugin_id: str, prefer: list[str]) -> bool:
    """Evaluates if a plugin_id matches any of the glob patterns specified in the prefer list."""
    if not prefer:
        return True
    for pat in prefer:
        if fnmatch.fnmatch(plugin_id, pat) or fnmatch.fnmatch(plugin_id.lower(), pat.lower()):
            return True
    return False


_NAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_]+")


@dataclass(frozen=True)
class BoundTool:
    """One resolved catalog op ready to be offered to an LLM + dispatched."""

    tool_name: str  # sanitized, unique, <=64 chars — what the LLM sees
    plugin_id: str
    operation_id: str
    description: str
    input_schema: dict


def _sanitize_tool_name(plugin_id: str, operation_id: str, *, taken: set[str]) -> str:
    # Regular plugin tools are already qualified as "{plugin_id}__{name}" by
    # static_tool_loader._qualify — don't double-prefix. Proxy op ids (e.g.
    # "search_repositories") are NOT plugin-qualified, so those still get the
    # plugin_id prefix for readability/uniqueness across mounted proxies.
    if operation_id.startswith(f"{plugin_id}__"):
        raw = operation_id
    else:
        raw = f"{plugin_id}__{operation_id}"
    name = _NAME_SAFE_RE.sub("_", raw)[:64]
    base = name
    i = 2
    while name in taken:
        suffix = f"_{i}"
        name = base[: 64 - len(suffix)] + suffix
        i += 1
    taken.add(name)
    return name


def build_toolset(
    refs: list[ToolRef],
    *,
    max_tools: int = 40,
    catalog: Any | None = None,
) -> list[BoundTool]:
    """Expand ToolRef globs against the live catalog into concrete BoundTools.

    Never raises — an unmatched ref is simply skipped (mirrors the discovery
    resolver's fail-open contract). Order is stable: refs in declared order,
    then catalog iteration order for glob matches.
    """
    try:
        if catalog is None:
            from core.route_registry.operation_catalog import get_operation_catalog

            catalog = get_operation_catalog()
        ops = list(catalog.all()) if catalog is not None else []
    except Exception as exc:
        logger.warning("agent_loop toolset: catalog unavailable: %s", exc)
        ops = []

    bound: list[BoundTool] = []
    taken: set[str] = set()
    seen_ids: set[tuple[str, str]] = set()

    for ref in refs:
        pid_pat = ref.plugin_id or "*"
        op_pat = ref.operation_id or "*"
        is_glob = any(ch in pid_pat for ch in "*?[") or any(ch in op_pat for ch in "*?[")

        for op in ops:
            pid = str(getattr(op, "plugin_id", "") or "")
            oid = str(getattr(op, "operation_id", "") or "")
            if not pid or not oid:
                continue
            if not (fnmatch.fnmatch(pid, pid_pat) or fnmatch.fnmatch(pid.lower(), pid_pat.lower())):
                continue
            if not (fnmatch.fnmatch(oid, op_pat) or fnmatch.fnmatch(oid.lower(), op_pat.lower())):
                continue
            key = (pid, oid)
            if key in seen_ids:
                continue
            seen_ids.add(key)

            schema = getattr(op, "input_schema", None) or {}
            if not isinstance(schema, dict):
                schema = {}
            desc = ref.description or str(getattr(op, "description", "") or "") or f"{pid}.{oid}"
            tool_name = _sanitize_tool_name(pid, oid, taken=taken)
            bound.append(
                BoundTool(
                    tool_name=tool_name,
                    plugin_id=pid,
                    operation_id=oid,
                    description=desc[:1000],
                    input_schema=schema,
                )
            )
            if len(bound) >= max_tools:
                return bound
            if not is_glob:
                # Exact (plugin_id, operation_id) ref — one match is enough.
                break

    return bound


def _sanitize_schema_for_llm(node: Any) -> Any:
    """Recursively coerce a JSON Schema into a shape every bound provider accepts.

    Real args are still validated against the *original* ``op.input_schema``
    by ``execute_operation`` at dispatch time — this only sanitizes what the
    LLM sees for tool-calling, so it's safe to be aggressive here. Concretely:
    some proxy/plugin schemas declare non-string ``enum`` values (e.g. a
    boolean-flag param as ``{"enum": [true, false]}``); Google's
    genai function-declaration schema only accepts string enum values and
    raises a pydantic ValidationError on anything else, which otherwise takes
    down the entire tool-calling turn for every bound tool, not just the
    offending one.
    """
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "enum" and isinstance(v, list):
                out[k] = [x if isinstance(x, str) else str(x).lower() if isinstance(x, bool) else str(x) for x in v]
            else:
                out[k] = _sanitize_schema_for_llm(v)
        return out
    if isinstance(node, list):
        return [_sanitize_schema_for_llm(x) for x in node]
    return node


def to_langchain_tool_schemas(tools: list[BoundTool]) -> list[dict]:
    """LangChain ``bind_tools``-compatible function-calling schema list."""
    out = []
    for t in tools:
        schema = t.input_schema if isinstance(t.input_schema, dict) and t.input_schema else {
            "type": "object",
            "properties": {},
        }
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.tool_name,
                    "description": t.description,
                    "parameters": _sanitize_schema_for_llm(schema),
                },
            }
        )
    return out
