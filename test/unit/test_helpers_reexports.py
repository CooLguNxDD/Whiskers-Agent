"""Regression guard: lock the public re-export surface of core_graph/node/helpers.py before
Phase 4 (the helpers.py split into submodules) so a dropped/renamed symbol fails loudly."""
import core_graph.node.helpers as helpers_mod
import core_graph.node as node_mod

GRAPH_SHIM_NAMES = [
    "compute_gate", "is_recoverable", "is_empty_result", "should_replan_on_empty",
    "build_replan_reset", "format_replan_context", "_step_has_dependents",
    "_build_failure_record", "_current_mcp_context", "_elicit", "_get_parameters_schema",
    "_param_hints", "_format_candidates", "_format_plugin_skills", "_parse_json_response",
    "_walk_path", "_deep_find_key", "_resolve_step_value", "_resolve_arg_bindings",
    "resolve_args_from_context", "_build_context_params", "_coerce", "_to_camel",
    "fill_missing_from_memory", "_normalize_arg_keys",
]

NODE_PACKAGE_NAMES = [
    "GraphRuntimeContext", "make_turn_init_node", "make_triage_node", "make_chat_node",
    "make_decompose_node", "make_embedder_node", "make_planner_node", "make_context_check_node", "make_builder_node",
    "make_step_resolver_node", "make_wait_node", "make_executor_node",
    "make_step_dispatcher_node", "make_validator_node", "make_retry_node", "make_confirm_node",
    "make_clarify_node", "make_round_summary_node", "make_summary_node", "make_goal_check_node", "make_goap_goal_node",
    "retry_router", "triage_router", "gate_router", "confirm_router", "clarify_router",
    "more_steps_router", "goal_check_router", "goap_goal_router", "context_resolution_router",
    "step_resolver_router", "permission_gate_router", "builder_router", "_execute_step",
]


def test_helpers_exposes_all_graph_shim_names():
    missing = [n for n in GRAPH_SHIM_NAMES if not hasattr(helpers_mod, n)]
    assert not missing, f"core_graph.node.helpers is missing names graph.py shims rely on: {missing}"


def test_node_package_exposes_all_public_names():
    missing = [n for n in NODE_PACKAGE_NAMES if not hasattr(node_mod, n)]
    assert not missing, f"core_graph.node is missing expected public names: {missing}"
