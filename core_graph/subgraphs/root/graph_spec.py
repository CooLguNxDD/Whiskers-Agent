"""Compose TRIAGE + CLASSIC + SPECIALIST fragments into ROOT_SPEC."""

from __future__ import annotations

from core_graph.graph_spec import GraphSpec
from core_graph.subgraphs.classic_goap.graph_spec import (
    CLASSIC_CONDITIONAL_EDGES,
    CLASSIC_NODES,
    CLASSIC_STATIC_EDGES,
)
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

ROOT_SPEC = GraphSpec(
    entry="turn_init",
    nodes=TRIAGE_NODES + CLASSIC_NODES + SPECIALIST_NODES,
    static_edges=TRIAGE_STATIC_EDGES + CLASSIC_STATIC_EDGES + SPECIALIST_STATIC_EDGES,
    conditional_edges=(
        TRIAGE_CONDITIONAL_EDGES + CLASSIC_CONDITIONAL_EDGES + SPECIALIST_CONDITIONAL_EDGES
    ),
)
