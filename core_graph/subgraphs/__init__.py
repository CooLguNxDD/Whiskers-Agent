"""Multi-stack graph packages: triage, classic_goap, specialist, oneshot_cli, root.

Topology ownership lives here; node factories stay in ``core_graph.node``.
Subgraph registration is centralized in ``registry`` (SubgraphRegistry).
"""

from __future__ import annotations

from core_graph.subgraphs.registry import (
    SubgraphRegistry,
    SubgraphSpec,
    clear_subgraphs,
    coerce_subgraph_spec,
    ensure_default_subgraphs,
    get_subgraph,
    get_subgraph_registry,
    list_subgraphs,
    register_subgraph,
    unregister_subgraph,
)

__all__ = [
    "ROOT_SPEC",
    "SubgraphSpec",
    "SubgraphRegistry",
    "get_subgraph_registry",
    "register_subgraph",
    "get_subgraph",
    "list_subgraphs",
    "unregister_subgraph",
    "clear_subgraphs",
    "ensure_default_subgraphs",
    "coerce_subgraph_spec",
]


def __getattr__(name: str):
    if name == "ROOT_SPEC":
        from core_graph.subgraphs.root.graph_spec import ROOT_SPEC

        return ROOT_SPEC
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
