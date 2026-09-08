"""Specialist cluster fragments: generic entry → summary or classic planning."""

from __future__ import annotations

from core_graph.graph_spec import ConditionalEdge, NodeSpec, planning_entry

SPECIALIST_NODES: tuple[NodeSpec, ...] = (
    NodeSpec("specialist_entry", "core_graph.node.specialist_entry.make_specialist_entry_node"),
)

SPECIALIST_STATIC_EDGES: tuple = ()

SPECIALIST_CONDITIONAL_EDGES: tuple[ConditionalEdge, ...] = (
    ConditionalEdge(
        "specialist_entry",
        "core_graph.node.routers.specialist_entry_router",
        {
            "plan": planning_entry,
            "done": "summary_node",
        },
    ),
)
