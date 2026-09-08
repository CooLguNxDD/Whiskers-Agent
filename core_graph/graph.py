"""
Dynamic LangGraph orchestrator — core feature.

Moved from plugins/pro_plugin/src/pro_graph/dynamic_graph.py (core_014) so any
plugin can drive natural-language API routing without depending on pro_plugin.

Flow (decompose-first, ``graph.decompose_first`` config flag, default on):
  decompose → embedder → planner → [gate] → builder → executor → validator → step_dispatcher
                                      ↓                            ↓
                                confirm_node                 (retry / halt / next_step)
                                      ↓
                                clarify_node → END

``decompose`` splits the request into sub-tasks so the embedder retrieves
candidates per sub-task; replan loops (clarify/goal-loop/validator-replan)
re-enter at ``decompose``. Flag-off restores the legacy embedder-first wiring.

Confidence gate:
  weighted score = 0.6 * pgvector_score + 0.4 * llm_confidence
  > 0.70   → execute
  0.50-0.70 → confirm
  < 0.50   → clarify

Long-chain safety override (Phase 5b): plans with >3 steps auto-route to
``confirm`` regardless of confidence so the user can vet a long super-agent
chain before it runs.

Auth resolution (Phase 5b): ``executor_node`` resolves headers via
``PluginRegistry.get_auth_headers(plugin_id)`` so the executor is plugin-
agnostic. ``plugin_id`` is carried on every RouteCandidate.

Graph topology is declared once in ``core_graph/graph_spec.py`` (``GRAPH_SPEC``) and built generically by ``build_from_spec``, which resolves node factories and routers from dotted-path strings via ``importlib`` and validates BFS-reachability before ``.compile()``. This replaces the earlier per-cluster registrar-file split.
"""

from langgraph.graph import END, START, StateGraph

from core_graph.states import DynamicAPIState

# Import helpers for re-export (shims)
from core_graph.node.helpers import (
    compute_gate,
    is_recoverable,
    is_empty_result,
    should_replan_on_empty,
    build_replan_reset,
    format_replan_context,
    _step_has_dependents,
    _build_failure_record,
    _current_mcp_context,
    _elicit,
    _get_parameters_schema,
    _param_hints,
    _format_candidates,
    _format_plugin_skills,
    _parse_json_response,
    _walk_path,
    _deep_find_key,
    _resolve_step_value,
    _resolve_arg_bindings,
    resolve_args_from_context,
    _build_context_params,
    _coerce,
    _to_camel,
    fill_missing_from_memory,
    _normalize_arg_keys,
)

from core_graph.node import GraphRuntimeContext

from core_graph.graph_spec import GRAPH_SPEC, build_from_spec

# Router re-export shim — these were implicitly public via the old fat import
# block; keep them here so existing callers (e.g. test_graph_helpers.py) don't
# break.  Do NOT remove without checking all importers.
from core_graph.node.routers import (  # noqa: F401
    retry_router,
    triage_router,
    gate_router,
    confirm_router,
    clarify_router,
    more_steps_router,
    goap_goal_router,
    context_resolution_router,
    step_resolver_router,
    permission_gate_router,
    builder_router,
)

# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_dynamic_graph(
    llm,
    *,
    context_params: dict | None = None,
    api_url: str = "",
    route_registry=None,
    checkpointer=None,
):
    """Compile the dynamic graph.

    Parameters
    ----------
    llm
        LangChain chat model (planner + builder).
    context_params
        Defaults passed into the builder prompt for path-param substitution.
        Plugin-agnostic — empty string falls back to whatever the user supplied
        in their query.
    api_url
        Base URL prepended to a route's ``path_template`` for dynamic-path
        execution. Empty string means the executor will require an absolute
        URL from the route descriptor.
    route_registry
        Optional ``RouteRegistry`` instance. When provided, the executor uses
        it to resolve auth headers via ``registry.get_auth_headers(plugin_id)``
        and to look up fast-path callables. When ``None``, the executor falls
        back to the per-plugin ``plugin_auth_headers`` shim (legacy path).
    """
    import os

    context_params = _build_context_params(
        overrides={k: v for k, v in (context_params or {}).items() if v}
    )

    if not api_url:
        api_url = os.environ.get("PLUGIN_API_URL", "")

    ctx = GraphRuntimeContext(
        llm=llm, 
        context_params=context_params, 
        api_url=api_url, 
        route_registry=route_registry, 
        checkpointer=checkpointer
    )

    graph = StateGraph(DynamicAPIState)

    build_from_spec(graph, ctx, GRAPH_SPEC)

    return graph.compile(checkpointer=checkpointer)
