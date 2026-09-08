"""Generic MCP-driven specialist agent — tool discovery + agent_loop execution."""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from core_graph.agent_loop import AgentRunResult, AgentSpec, ToolRef, run_agent
from core_graph.subgraphs.specialist.prompts.specialist_prompt import SPECIALIST_SYSTEM_PROMPT

logger = logging.getLogger("whiskers.core_graph.specialist.agent")

# Default globs when caller does not constrain tools — read-shaped only,
# and deliberately plugin-agnostic (data-configurable via
# config/server_config.json:specialist.default_tool_globs). A plugin that
# wants a richer default toolset should declare a FlowSpec
# (core_graph.subgraphs.specialist.flow_spec) with its own tool_globs instead
# of core hardcoding that plugin's name here.
_FALLBACK_TOOL_GLOBS: tuple[str, ...] = (
    "*/*search*",
    "*/*list*",
    "*/*get*",
    "*/*fetch*",
    "proxy_*/*read*",
)


def _default_tool_globs() -> tuple[str, ...]:
    """Read ``specialist.default_tool_globs`` from server_config.json, else the built-in fallback."""
    try:
        from utils.config_registry import get_config_registry

        globs = get_config_registry().server.get("specialist", {}).get("default_tool_globs")
        if isinstance(globs, list) and globs:
            return tuple(str(g) for g in globs if str(g).strip())
    except Exception as exc:
        logger.debug("specialist default_tool_globs config unavailable: %s", exc)
    return _FALLBACK_TOOL_GLOBS


def parse_tool_globs(globs: list[str] | None) -> list[ToolRef]:
    """Parse ``plugin/op`` or bare patterns into ToolRefs.

    Accepted forms:
    - ``plugin_id/operation_glob`` (preferred)
    - ``plugin_id:operation_glob``
    - ``plugin_glob`` alone → operation ``*``
    - ``*foo*`` alone → plugin ``*``, operation ``*foo*``
    """
    if not globs:
        globs = list(_default_tool_globs())
    refs: list[ToolRef] = []
    seen: set[tuple[str, str]] = set()
    for raw in globs:
        s = (raw or "").strip()
        if not s:
            continue
        if "/" in s:
            pid, _, op = s.partition("/")
        elif ":" in s:
            pid, _, op = s.partition(":")
        elif any(ch in s for ch in "*?[") and not s.startswith("proxy_"):
            # bare op-style glob
            pid, op = "*", s
        else:
            pid, op = s, "*"
        pid = (pid or "*").strip() or "*"
        op = (op or "*").strip() or "*"
        key = (pid, op)
        if key in seen:
            continue
        seen.add(key)
        refs.append(ToolRef(plugin_id=pid, operation_id=op))
    return refs


def discover_tools_from_catalog(
    goal: str,
    *,
    tool_globs: list[str] | None = None,
    max_tools: int = 40,
) -> list[ToolRef]:
    """Resolve ToolRefs from globs + optional catalog keyword match on the goal.

    Primary filter is ``tool_globs``. When the catalog is available, ops whose
    operation_id or description share tokens with the goal are preferred in
    order (still constrained by globs).
    """
    base_refs = parse_tool_globs(tool_globs)
    tokens = {
        t.lower()
        for t in (goal or "").replace("/", " ").replace("-", " ").split()
        if len(t) >= 3
    }

    try:
        from core.route_registry.operation_catalog import get_operation_catalog

        catalog = get_operation_catalog()
        ops = list(catalog.all()) if catalog is not None else []
    except Exception as exc:
        logger.debug("specialist catalog unavailable: %s", exc)
        return base_refs

    if not ops:
        return base_refs

    # Expand globs against catalog, score by goal token overlap.
    scored: list[tuple[int, str, str]] = []
    for ref in base_refs:
        pid_pat = ref.plugin_id or "*"
        op_pat = ref.operation_id or "*"
        for op in ops:
            pid = str(getattr(op, "plugin_id", "") or "")
            oid = str(getattr(op, "operation_id", "") or "")
            if not pid or not oid:
                continue
            if not (
                fnmatch.fnmatch(pid, pid_pat)
                or fnmatch.fnmatch(pid.lower(), pid_pat.lower())
            ):
                continue
            if not (
                fnmatch.fnmatch(oid, op_pat)
                or fnmatch.fnmatch(oid.lower(), op_pat.lower())
            ):
                continue
            desc = str(getattr(op, "description", "") or "").lower()
            blob = f"{pid} {oid} {desc}".lower()
            score = sum(1 for t in tokens if t in blob) if tokens else 0
            scored.append((score, pid, oid))

    if not scored:
        return base_refs

    # Prefer higher score; keep unique (plugin, op); cap at max_tools.
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    out: list[ToolRef] = []
    seen_ids: set[tuple[str, str]] = set()
    for _score, pid, oid in scored:
        key = (pid, oid)
        if key in seen_ids:
            continue
        seen_ids.add(key)
        out.append(ToolRef(plugin_id=pid, operation_id=oid))
        if len(out) >= max_tools:
            break
    return out or base_refs


async def run_specialist_agent(
    goal: str,
    *,
    tenant_id: int,
    tool_globs: list[str] | None = None,
    system_prompt: str | None = None,
    plugin_context: dict[str, Any] | None = None,
    max_steps: int = 12,
    max_seconds: float = 90.0,
    max_tools: int = 40,
    caller_scopes: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    model: str | None = None,
) -> AgentRunResult:
    """Discover tools, run the generic agent loop, return AgentRunResult.

    Never raises — degrades to status=error via run_agent.
    """
    q = (goal or "").strip()
    tools = discover_tools_from_catalog(q, tool_globs=tool_globs, max_tools=max_tools)
    prompt = (system_prompt or SPECIALIST_SYSTEM_PROMPT).strip()
    if plugin_context:
        try:
            import json

            ctx_blob = json.dumps(plugin_context, default=str)[:4000]
            prompt = f"{prompt}\n\nPlugin context (JSON):\n{ctx_blob}"
        except Exception as exc:
            # Non-serializable context is optional enrichment only.
            logger.debug("specialist plugin_context dump skipped: %s", exc)

    spec = AgentSpec(
        name="specialist",
        system_prompt=prompt,
        tools=tools,
        max_steps=max_steps,
        max_seconds=max_seconds,
        max_tools=max_tools,
        caller_scopes=caller_scopes,
        output_schema=output_schema,
        model=model,
    )
    logger.info(
        "run_specialist_agent goal=%r tools=%d globs=%s",
        q[:80],
        len(tools),
        (tool_globs or ["<default>"])[:6],
    )
    return await run_agent(spec, q, tenant_id=tenant_id, context=plugin_context)
