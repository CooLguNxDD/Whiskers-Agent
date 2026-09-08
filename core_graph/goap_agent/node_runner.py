"""Resolve graph node factories and invoke them against session state."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
from typing import Any, Awaitable, Callable

from core_graph.graph_spec import GRAPH_SPEC, NodeSpec
from core_graph.node.context import GraphRuntimeContext

logger = logging.getLogger("whiskers.goap_agent")

NodeFn = Callable[[dict], Awaitable[dict]]

_ctx: GraphRuntimeContext | None = None
_nodes: dict[str, NodeFn] | None = None
_init_lock = asyncio.Lock()


def list_node_names(*, resolve_when: bool = True) -> list[str]:
    """Return active GRAPH_SPEC node names (optionally evaluating when predicates)."""
    names: list[str] = []
    for spec in GRAPH_SPEC.nodes:
        if resolve_when and spec.when is not None:
            try:
                if not spec.when():
                    continue
            except Exception as exc:
                logger.debug("list_node_names: when() failed for %s: %s", spec.name, exc)
                continue
        names.append(spec.name)
    return names


def _load_factory(spec: NodeSpec) -> Callable[[GraphRuntimeContext], NodeFn]:
    """Import a node factory from a dotted path like core_graph.node.make_planner_node."""
    path = spec.factory
    if "." not in path:
        raise ValueError(f"Invalid node factory path: {path}")
    mod_path, attr = path.rsplit(".", 1)
    # Factories may live under core_graph.node or a submodule (permission_gate).
    try:
        mod = importlib.import_module(mod_path)
    except ModuleNotFoundError:
        # Fallback: core_graph.node re-exports most factories
        if mod_path.startswith("core_graph.node."):
            mod = importlib.import_module("core_graph.node")
        else:
            raise
    factory = getattr(mod, attr, None)
    if factory is None and mod_path != "core_graph.node":
        mod = importlib.import_module("core_graph.node")
        factory = getattr(mod, attr, None)
    if not callable(factory):
        raise AttributeError(f"Node factory not found: {path}")
    return factory


async def get_runtime_context(*, force_reload: bool = False) -> GraphRuntimeContext:
    """Build (once) the GraphRuntimeContext shared by GoapAgent node tools."""
    global _ctx, _nodes
    if _ctx is not None and not force_reload:
        return _ctx
    async with _init_lock:
        if _ctx is not None and not force_reload:
            return _ctx
        from core.llm_config_service import get_graph_core_llm
        from core.context import route_registry
        from core_graph.node.helpers.args import _build_context_params

        llm = await get_graph_core_llm()
        api_url = os.environ.get("PLUGIN_API_URL", "")
        context_params = _build_context_params()
        _ctx = GraphRuntimeContext(
            llm=llm,
            context_params=context_params,
            api_url=api_url,
            route_registry=route_registry,
            checkpointer=None,
        )
        _nodes = None  # rebuild node map with new ctx
        return _ctx


def invalidate_runtime() -> None:
    """Drop cached context/nodes so the next invoke rebuilds (LLM pool swap)."""
    global _ctx, _nodes
    _ctx = None
    _nodes = None


async def get_node_map(*, force_reload: bool = False) -> dict[str, NodeFn]:
    """Instantiate all GRAPH_SPEC node callables for the current runtime context."""
    global _nodes
    ctx = await get_runtime_context(force_reload=force_reload)
    if _nodes is not None and not force_reload:
        return _nodes
    async with _init_lock:
        if _nodes is not None and not force_reload:
            return _nodes
        built: dict[str, NodeFn] = {}
        for spec in GRAPH_SPEC.nodes:
            if spec.when is not None:
                try:
                    if not spec.when():
                        continue
                except Exception as exc:
                    logger.warning("goap_agent: when() failed for %s: %s", spec.name, exc)
                    continue
            try:
                factory = _load_factory(spec)
                built[spec.name] = factory(ctx)
            except Exception as exc:
                logger.exception("goap_agent: failed to build node %s: %s", spec.name, exc)
        _nodes = built
        return _nodes


async def invoke_node(node_name: str, state: dict[str, Any]) -> dict[str, Any]:
    """Run one graph node; return the partial state delta (not full state)."""
    nodes = await get_node_map()
    fn = nodes.get(node_name)
    if fn is None:
        available = sorted(nodes.keys())
        raise KeyError(f"Unknown or inactive node '{node_name}'. Available: {available}")
    result = await fn(state)
    if result is None:
        return {}
    if not isinstance(result, dict):
        return {"response": {"status": "error", "message": f"Node {node_name} returned non-dict"}}
    return result
