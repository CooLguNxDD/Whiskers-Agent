"""
core_graph.node.helpers — package facade.

Split (Phase 4 modularity refactor) from a single 903-line helpers.py into themed submodules:
- _shared.py   — constants shared across the other submodules
- mcp_ctx.py   — FastMCP context/elicitation helpers, candidate formatting, JSON-response parsing
- gating.py    — confidence gate, retry/replan routing decisions, empty-result detection
- replan.py    — replan failure-record building and context formatting
- args.py      — $steps[] binding resolution, working-memory fallback, param coercion
- routing.py   — route-candidate adaptation, read/write classification, token-usage accumulation

Re-exports every name (function, class, constant — underscore-prefixed or not) that existed on the
original single-file helpers.py via __all__, so the star-import in core_graph/node/__init__.py and
every direct `from core_graph.node.helpers import X` across the codebase keep resolving unchanged.
"""
from core_graph.node.helpers._shared import (
    _RESULT_ENVELOPE_KEYS,
    _BINDING_RE,
    _ID_ALIASES,
    _MAX_DEEP_FIND_DEPTH,
)
from core_graph.node.helpers.mcp_ctx import (
    _current_mcp_context,
    _has_progress_channel,
    _elicit_keepalive,
    _elicit,
    _get_parameters_schema,
    _param_hints,
    _format_candidates,
    _format_plugin_skills,
    _parse_json_response,
    normalize_tool_card,
)
from core_graph.node.helpers.gating import (
    LONG_CHAIN_THRESHOLD,
    CONFIDENCE_EXECUTE_THRESHOLD,
    CONFIDENCE_CONFIRM_THRESHOLD,
    compute_gate,
    is_recoverable,
    _normalize_param_name,
    detect_failed_params,
    is_empty_result,
    _step_has_dependents,
    should_replan_on_empty,
    retry_router,
)
from core_graph.node.helpers.replan import (
    _build_failure_record,
    format_replan_context,
    build_replan_reset,
)
from core_graph.node.helpers.args import (
    _walk_path,
    _deep_find_key,
    harvest_all_ids_from_envelope,
    _resolve_step_value,
    _resolve_arg_bindings,
    resolve_args_from_context,
    _build_context_params,
    _coerce,
    _to_camel,
    fill_missing_from_memory,
    _normalize_arg_keys,
)
from core_graph.node.helpers.routing import (
    _descriptor_to_candidate,
    _WRITE_METHODS,
    operation_class,
    resolve_route_candidate,
    fold_token_usage,
)

__all__ = [
    "_RESULT_ENVELOPE_KEYS", "_BINDING_RE", "_ID_ALIASES", "_MAX_DEEP_FIND_DEPTH",
    "_current_mcp_context", "_has_progress_channel", "_elicit_keepalive", "_elicit",
    "_get_parameters_schema", "_param_hints", "_format_candidates", "_format_plugin_skills",
    "_parse_json_response", "normalize_tool_card",
    "LONG_CHAIN_THRESHOLD", "CONFIDENCE_EXECUTE_THRESHOLD", "CONFIDENCE_CONFIRM_THRESHOLD",
    "compute_gate", "is_recoverable", "_normalize_param_name", "detect_failed_params",
    "is_empty_result", "_step_has_dependents", "should_replan_on_empty", "retry_router",
    "_build_failure_record", "format_replan_context", "build_replan_reset",
    "_walk_path", "_deep_find_key", "harvest_all_ids_from_envelope", "_resolve_step_value",
    "_resolve_arg_bindings", "resolve_args_from_context", "_build_context_params", "_coerce",
    "_to_camel", "fill_missing_from_memory", "_normalize_arg_keys",
    "_descriptor_to_candidate", "_WRITE_METHODS", "operation_class", "resolve_route_candidate",
    "fold_token_usage",
]
