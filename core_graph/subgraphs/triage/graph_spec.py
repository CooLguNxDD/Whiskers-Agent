"""Triage cluster node/edge fragments for ROOT_SPEC composition."""

from __future__ import annotations

from core_graph.graph_spec import ConditionalEdge, NodeSpec, StaticEdge, planning_entry

TRIAGE_NODES: tuple[NodeSpec, ...] = (
    NodeSpec("turn_init", "core_graph.node.make_turn_init_node"),
    NodeSpec("triage", "core_graph.node.make_triage_node"),
    NodeSpec("chat_node", "core_graph.node.make_chat_node"),
)

TRIAGE_STATIC_EDGES: tuple[StaticEdge, ...] = (
    StaticEdge("turn_init", "triage"),
    StaticEdge("chat_node", "END"),
)

# classic → planning entry; specialist → specialist_entry; chat → chat_node.
# ``task`` kept as alias key for older router tests (maps same as classic).
TRIAGE_CONDITIONAL_EDGES: tuple[ConditionalEdge, ...] = (
    ConditionalEdge(
        "triage",
        "core_graph.node.routers.triage_router",
        {
            "chat": "chat_node",
            "classic": planning_entry,
            "specialist": "specialist_entry",
            # back-compat if any caller still emits task
            "task": planning_entry,
        },
    ),
)
