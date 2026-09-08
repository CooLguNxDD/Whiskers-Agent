"""Classic GOAP cluster fragments (planning through goal loop)."""

from __future__ import annotations

from core_graph.graph_spec import (
    ConditionalEdge,
    NodeSpec,
    StaticEdge,
    decompose_first_enabled,
    planning_entry,
    replan_entry,
)

CLASSIC_NODES: tuple[NodeSpec, ...] = (
    NodeSpec("decompose", "core_graph.node.make_decompose_node", when=decompose_first_enabled),
    NodeSpec("embedder", "core_graph.node.make_embedder_node"),
    NodeSpec("planner", "core_graph.node.make_planner_node"),
    NodeSpec("context_check", "core_graph.node.make_context_check_node"),
    NodeSpec("step_resolver", "core_graph.node.make_step_resolver_node"),
    NodeSpec("confirm_node", "core_graph.node.make_confirm_node"),
    NodeSpec("clarify_node", "core_graph.node.make_clarify_node"),
    NodeSpec("permission_gate", "core_graph.node.permission_gate.make_permission_gate_node"),
    NodeSpec("builder", "core_graph.node.make_builder_node"),
    NodeSpec("wait_node", "core_graph.node.make_wait_node"),
    NodeSpec("executor", "core_graph.node.make_executor_node"),
    NodeSpec("validator", "core_graph.node.make_validator_node"),
    NodeSpec("retry_node", "core_graph.node.make_retry_node"),
    NodeSpec("step_dispatcher", "core_graph.node.make_step_dispatcher_node"),
    NodeSpec("round_summary", "core_graph.node.make_round_summary_node"),
    NodeSpec("summary_node", "core_graph.node.make_summary_node"),
    NodeSpec("goap_goal", "core_graph.node.make_goap_goal_node"),
)

CLASSIC_STATIC_EDGES: tuple[StaticEdge, ...] = (
    StaticEdge("decompose", "embedder", when=decompose_first_enabled),
    StaticEdge("embedder", "planner"),
    StaticEdge("wait_node", "validator"),
    StaticEdge("executor", "validator"),
    StaticEdge("round_summary", "goap_goal"),
    StaticEdge("summary_node", "END"),
)

CLASSIC_CONDITIONAL_EDGES: tuple[ConditionalEdge, ...] = (
    ConditionalEdge(
        "planner",
        "core_graph.node.routers.gate_router",
        {
            "context_check": "context_check",
            "confirm_node": "confirm_node",
            "clarify_node": "clarify_node",
            "done": "END",
        },
    ),
    ConditionalEdge(
        "confirm_node",
        "core_graph.node.routers.confirm_router",
        {"execute": "context_check", "done": "END"},
    ),
    ConditionalEdge(
        "clarify_node",
        "core_graph.node.routers.clarify_router",
        {"replan": planning_entry, "done": "END"},
    ),
    ConditionalEdge(
        "context_check",
        "core_graph.node.routers.context_resolution_router",
        {"builder": "permission_gate", "step_resolver": "step_resolver"},
    ),
    ConditionalEdge(
        "step_resolver",
        "core_graph.node.routers.step_resolver_router",
        {"builder": "permission_gate", "done": "END", "goap_goal": "goap_goal"},
    ),
    ConditionalEdge(
        "builder",
        "core_graph.node.routers.builder_router",
        {"executor": "executor", "wait_node": "wait_node"},
    ),
    ConditionalEdge(
        "validator",
        "core_graph.node.routers.retry_router",
        {
            "next_step": "step_dispatcher",
            "retry": "retry_node",
            "replan": replan_entry,
            "halt": "END",
            "recover": "goap_goal",
        },
    ),
    ConditionalEdge(
        "retry_node",
        "core_graph.node.routers.context_resolution_router",
        {"builder": "permission_gate", "step_resolver": "step_resolver"},
    ),
    ConditionalEdge(
        "step_dispatcher",
        "core_graph.node.routers.more_steps_router",
        {
            "context_check": "context_check",
            "summary": "round_summary",
            "done": "END",
            "recover": "goap_goal",
        },
    ),
    ConditionalEdge(
        "permission_gate",
        "core_graph.node.routers.permission_gate_router",
        {"builder": "builder", "done": "END"},
    ),
    ConditionalEdge(
        "goap_goal",
        "core_graph.node.routers.goap_goal_router",
        {
            "continue": planning_entry,
            "done": "summary_node",
            "retriage": "triage",
        },
    ),
)
