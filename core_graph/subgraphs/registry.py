"""Central registry for graph subgraphs (root, triage, classic, specialist, plugin).

Mirrors patterns from ``core_graph.runtime.registry`` and ``WorkerRegistry``:
register / get / list / unregister / clear with a process-wide singleton.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Callable

logger = logging.getLogger("whiskers.core_graph.subgraphs.registry")


@dataclass(frozen=True)
class SubgraphSpec:
    """Declarative registration for one subgraph or domain agent surface.

    Built-in stacks typically set ``graph_spec`` (full ``GraphSpec``) and/or
    fragment ``nodes`` / edge tuples. Plugin domain agents may set only
    ``handler`` + ``metadata`` (e.g. portfolio specialist pipeline).
    """

    id: str
    name: str
    description: str = ""
    graph_spec: Any | None = None  # GraphSpec | None — Any avoids circular import
    nodes: tuple | list | None = None
    static_edges: tuple | list | None = None
    conditional_edges: tuple | list | None = None
    entry: str | None = None
    handler: Callable[..., Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Subgraph-level model selection default for its agentic work — a
    # ModelRoleSpec role_id (resolved via "role:<id>", first rung only) and/or
    # a raw effort:<level> vocabulary. Consulted by
    # core_graph.model_roles.selection.select_agent_model as the second-to-last
    # rung of the specialist-stack precedence chain (see selection.py).
    model_role: str | None = None
    effort: str | None = None


class SubgraphRegistry:
    """In-memory map of subgraph id → SubgraphSpec."""

    def __init__(self) -> None:
        self._specs: dict[str, SubgraphSpec] = {}

    def register(self, spec: SubgraphSpec) -> SubgraphSpec:
        """Add or replace a subgraph spec by id."""
        if not isinstance(spec, SubgraphSpec):
            raise TypeError("Only SubgraphSpec instances can be registered.")
        sid = (spec.id or "").strip()
        if not sid:
            raise ValueError("SubgraphSpec.id is required")
        if sid != spec.id:
            spec = replace(spec, id=sid)
        self._specs[sid] = spec
        logger.debug("subgraph registered: %s", sid)
        return spec

    def get(self, subgraph_id: str) -> SubgraphSpec | None:
        """Return a registered subgraph or None."""
        return self._specs.get(subgraph_id)

    def list_subgraphs(self) -> list[SubgraphSpec]:
        """Return all registered specs sorted by id."""
        return sorted(self._specs.values(), key=lambda s: s.id)

    def unregister(self, subgraph_id: str) -> bool:
        """Remove a subgraph by id. Returns True if it was present."""
        return self._specs.pop(subgraph_id, None) is not None

    def clear(self) -> None:
        """Remove all registrations (tests only)."""
        self._specs.clear()

    def has(self, subgraph_id: str) -> bool:
        """True when ``subgraph_id`` is registered."""
        return subgraph_id in self._specs


_REGISTRY: SubgraphRegistry | None = None
_DEFAULTS_SEEDED = False


def get_subgraph_registry() -> SubgraphRegistry:
    """Process-wide SubgraphRegistry singleton."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SubgraphRegistry()
    return _REGISTRY


def register_subgraph(spec: SubgraphSpec) -> SubgraphSpec:
    """Register or replace a subgraph on the global registry."""
    return get_subgraph_registry().register(spec)


def get_subgraph(subgraph_id: str) -> SubgraphSpec | None:
    """Look up a subgraph by id on the global registry."""
    return get_subgraph_registry().get(subgraph_id)


def list_subgraphs() -> list[SubgraphSpec]:
    """List all registered subgraphs (sorted by id)."""
    return get_subgraph_registry().list_subgraphs()


def unregister_subgraph(subgraph_id: str) -> bool:
    """Unregister a subgraph by id from the global registry."""
    return get_subgraph_registry().unregister(subgraph_id)


def clear_subgraphs() -> None:
    """Clear the global registry and reset default-seed flag (tests only)."""
    global _DEFAULTS_SEEDED
    get_subgraph_registry().clear()
    _DEFAULTS_SEEDED = False


def ensure_default_subgraphs() -> None:
    """Register built-in subgraphs if not already present (idempotent per id).

    Always (re)registers the five core ids so callers after ``clear_subgraphs``
    get a full set. Plugin-contributed ids are left alone.
    """
    global _DEFAULTS_SEEDED
    reg = get_subgraph_registry()

    from core_graph.subgraphs.classic_goap.graph_spec import (
        CLASSIC_CONDITIONAL_EDGES,
        CLASSIC_NODES,
        CLASSIC_STATIC_EDGES,
    )
    from core_graph.subgraphs.root.graph_spec import ROOT_SPEC
    from core_graph.subgraphs.specialist.graph_spec import (
        SPECIALIST_CONDITIONAL_EDGES,
        SPECIALIST_NODES,
        SPECIALIST_STATIC_EDGES,
    )
    from core_graph.subgraphs.triage.graph_spec import (
        TRIAGE_CONDITIONAL_EDGES,
        TRIAGE_NODES,
        TRIAGE_STATIC_EDGES,
    )

    reg.register(
        SubgraphSpec(
            id="root",
            name="Root Graph",
            description="Unified native graph: triage ⊕ classic GOAP ⊕ specialist",
            graph_spec=ROOT_SPEC,
            nodes=ROOT_SPEC.nodes,
            static_edges=ROOT_SPEC.static_edges,
            conditional_edges=ROOT_SPEC.conditional_edges,
            entry=ROOT_SPEC.entry,
            metadata={"builtin": True, "kind": "graph"},
        )
    )
    reg.register(
        SubgraphSpec(
            id="triage",
            name="Triage Cluster",
            description="turn_init → triage → chat | classic | specialist",
            nodes=TRIAGE_NODES,
            static_edges=TRIAGE_STATIC_EDGES,
            conditional_edges=TRIAGE_CONDITIONAL_EDGES,
            entry="turn_init",
            metadata={"builtin": True, "kind": "fragment"},
        )
    )
    reg.register(
        SubgraphSpec(
            id="classic_goap",
            name="Classic GOAP",
            description="Planning through goal loop (embedder/planner/executor…)",
            nodes=CLASSIC_NODES,
            static_edges=CLASSIC_STATIC_EDGES,
            conditional_edges=CLASSIC_CONDITIONAL_EDGES,
            entry=None,  # planning_entry is dynamic
            metadata={"builtin": True, "kind": "fragment"},
        )
    )
    reg.register(
        SubgraphSpec(
            id="specialist",
            name="Specialist Stack",
            description="Generic MCP specialist entry + domain registry",
            nodes=SPECIALIST_NODES,
            static_edges=SPECIALIST_STATIC_EDGES,
            conditional_edges=SPECIALIST_CONDITIONAL_EDGES,
            entry="specialist_entry",
            metadata={"builtin": True, "kind": "fragment"},
        )
    )
    reg.register(
        SubgraphSpec(
            id="oneshot_cli",
            name="One-shot CLI",
            description="Mode B CLI meta-stack (claude/agy/grok oneshot routing)",
            metadata={
                "builtin": True,
                "kind": "meta",
                "module": "core_graph.subgraphs.oneshot_cli",
            },
        )
    )
    _DEFAULTS_SEEDED = True
    logger.debug("default subgraphs ensured (%d total)", len(reg.list_subgraphs()))


def coerce_subgraph_spec(
    raw: SubgraphSpec | dict[str, Any],
    *,
    plugin_id: str | None = None,
) -> SubgraphSpec:
    """Build a SubgraphSpec from a dict or pass through an existing instance.

    Stamps ``metadata.plugin_id`` when ``plugin_id`` is provided.
    """
    if isinstance(raw, SubgraphSpec):
        spec = raw
    elif isinstance(raw, dict):
        meta = dict(raw.get("metadata") or {})
        spec = SubgraphSpec(
            id=str(raw.get("id") or "").strip(),
            name=str(raw.get("name") or raw.get("id") or "").strip() or "unnamed",
            description=str(raw.get("description") or ""),
            graph_spec=raw.get("graph_spec"),
            nodes=raw.get("nodes"),
            static_edges=raw.get("static_edges"),
            conditional_edges=raw.get("conditional_edges"),
            entry=raw.get("entry"),
            handler=raw.get("handler"),
            metadata=meta,
            model_role=raw.get("model_role"),
            effort=raw.get("effort"),
        )
    else:
        raise TypeError(f"contribute_subgraph expects SubgraphSpec or dict, got {type(raw)}")

    if plugin_id:
        meta = dict(spec.metadata or {})
        meta.setdefault("plugin_id", plugin_id)
        spec = replace(spec, metadata=meta)
    return spec
