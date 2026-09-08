"""Domain handler registry for the specialist graph entrypoint.

Plugins (e.g. portfolio_plugin) register a domain runner that owns multi-phase
orchestration. When no domain claims the goal, the generic MCP specialist
pipeline runs instead.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("whiskers.core_graph.specialist.registry")

# Domain runner: return a response envelope dict, or None to decline.
DomainRunner = Callable[..., Awaitable[dict[str, Any] | None]]

_runners: list[tuple[str, DomainRunner]] = []


def register_specialist_domain(name: str, runner: DomainRunner) -> None:
    """Register (or replace) a named specialist domain handler.

    Later registrations are tried first (LIFO) so tests/plugins can override.
    """
    global _runners
    n = (name or "").strip()
    if not n or runner is None:
        return
    _runners = [(k, r) for k, r in _runners if k != n]
    _runners.append((n, runner))
    logger.info("specialist domain registered: %s", n)


def unregister_specialist_domain(name: str) -> bool:
    """Remove a domain handler by name. Returns True if it was present."""
    global _runners
    before = len(_runners)
    _runners = [(k, r) for k, r in _runners if k != name]
    return len(_runners) < before


def clear_specialist_domains() -> None:
    """Remove all domain handlers (tests only)."""
    global _runners
    _runners = []


def list_specialist_domains() -> list[str]:
    """Return registered domain names (registration order; last tried first)."""
    return [k for k, _ in _runners]


async def run_specialist(
    goal: str,
    *,
    tenant_id: int,
    session_id: str | None = None,
    tool_globs: list[str] | None = None,
    system_prompt: str | None = None,
    plugin_context: dict[str, Any] | None = None,
    goal_class: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Select a declarative FlowSpec first; else try domain runners (LIFO);
    else fall back to the generic MCP specialist pipeline.

    FlowSpecs are the spec+MCP-driven replacement for hand-written domain
    runners (see ``core_graph.subgraphs.specialist.flow_spec``): a plugin with
    no registered flows is completely unaffected — this is purely additive.
    Domain runners may return ``None`` to decline (next runner / generic).
    """
    from core_graph.subgraphs.specialist.flow_registry import select_flow
    from core_graph.subgraphs.specialist.flow_runner import run_flow
    from core_graph.subgraphs.specialist.pipeline import run_specialist_pipeline

    q = (goal or "").strip()

    flow = select_flow(q, goal_class=goal_class)
    if flow is not None:
        try:
            return await run_flow(
                flow,
                q,
                tenant_id=tenant_id,
                session_id=session_id,
                caller_scopes=kwargs.get("caller_scopes"),
                inputs=plugin_context,
            )
        except Exception as exc:
            logger.warning("flow %s raised unexpectedly, falling back: %s", flow.flow_id, exc, exc_info=True)

    # LIFO: last registered domain wins first claim.
    for name, runner in reversed(list(_runners)):
        try:
            result = await runner(
                q,
                tenant_id=tenant_id,
                session_id=session_id,
                tool_globs=tool_globs,
                system_prompt=system_prompt,
                plugin_context=plugin_context,
                **kwargs,
            )
        except Exception as exc:
            logger.warning("specialist domain %s failed: %s", name, exc, exc_info=True)
            continue
        if result is not None:
            if isinstance(result, dict) and "specialist" not in result:
                result = {**result, "specialist": True, "specialist_domain": name}
            elif isinstance(result, dict):
                result.setdefault("specialist_domain", name)
            return result

    # No FlowSpec/domain runner claimed the goal — generic pipeline. Model
    # selection here has no flow/stage to consult, so it's just the
    # subgraph-level default (SubgraphSpec.model_role/effort for the
    # "specialist" subgraph id, if a plugin declared one) falling through to
    # the core "specialist" role (§8.5 rungs 6-7 of select_agent_model).
    from core_graph.model_roles.selection import select_agent_model
    from core_graph.subgraphs.registry import get_subgraph

    sub = get_subgraph("specialist")
    model = select_agent_model(
        goal_class=goal_class,
        subgraph_model_role=sub.model_role if sub else None,
        subgraph_effort=sub.effort if sub else None,
    )
    return await run_specialist_pipeline(
        q,
        tenant_id=tenant_id,
        session_id=session_id,
        tool_globs=tool_globs,
        system_prompt=system_prompt,
        plugin_context=plugin_context,
        model=model,
        **kwargs,
    )
