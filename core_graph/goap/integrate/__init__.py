"""
core_graph.goap.integrate — package facade.

Split (Phase 5 modularity refactor) from a single 893-line integrate.py into themed submodules:
- _shared.py    — constants shared across the other submodules
- goal_parsing.py — LLM goal-extraction response parsing
- seed_world.py — GOAP world-state seeding and planning entrypoint
- fanout.py     — fan-out detection, injection, and step-collapsing
- state.py      — GoapPlan -> DynamicAPIState fragment conversion
- scheduling.py — step task classification, model assignment, parallel-group derivation

Re-exports every name that existed on the original single-file integrate.py via __all__, so all
existing `from core_graph.goap.integrate import X` callers (core_graph/node/planner.py,
core_graph/node/builder.py, core_graph/goap/goal_loop.py, and several test files) keep resolving
unchanged.
"""
from core_graph.goap.integrate._shared import _INSTRUCTION_PARAM_NAMES, _SEARCH_PARAM_NAMES
from core_graph.goap.integrate.goal_parsing import (
    parse_goal_extraction,
    parse_goal_extraction_extended,
    parse_seed_values,
)
from core_graph.goap.integrate.seed_world import (
    build_seed_world,
    fill_literal_args,
    goap_plan_from_goal,
)
from core_graph.goap.integrate.seed_hygiene import (
    enrich_instruction_value,
    apply_followup_search_topic,
    is_bare_search_followup,
    is_instruction_param,
    is_weak_instruction_value,
    resolve_search_query,
    sanitize_seed_values,
    strip_failed_seed_keys,
    is_id_like_param,
    looks_like_real_id,
)
from core_graph.goap.integrate.chain_repair import repair_collection_consumer_chain
from core_graph.goap.integrate.fanout import (
    detect_fanout_count,
    _strip_op_prefix,
    _id_param_of,
    _find_fetch_step,
    _normalize_item_bindings,
    _inject_defensive_fanout,
    inject_literal_list_fanout,
    collapse_homogeneous_fanout,
)
from core_graph.goap.integrate.state import goap_steps_to_state
from core_graph.goap.integrate.scheduling import (
    _classify_step_task,
    assign_step_models,
    derive_parallel_groups,
)

__all__ = [
    "_SEARCH_PARAM_NAMES", "_INSTRUCTION_PARAM_NAMES",
    "parse_goal_extraction", "parse_goal_extraction_extended", "parse_seed_values",
    "build_seed_world", "fill_literal_args", "goap_plan_from_goal",
    "sanitize_seed_values", "strip_failed_seed_keys", "is_id_like_param", "looks_like_real_id",
    "is_instruction_param", "is_weak_instruction_value", "enrich_instruction_value",
    "is_bare_search_followup", "resolve_search_query", "apply_followup_search_topic",
    "repair_collection_consumer_chain",
    "detect_fanout_count", "_strip_op_prefix", "_id_param_of", "_find_fetch_step",
    "_normalize_item_bindings", "_inject_defensive_fanout", "inject_literal_list_fanout",
    "collapse_homogeneous_fanout",
    "goap_steps_to_state",
    "_classify_step_task", "assign_step_models", "derive_parallel_groups",
]
