"""
State definitions for the dynamic LangGraph orchestrator.

Moved from plugins/pro_plugin/src/pro_graph/dynamic_states.py (core_014).
Extended in Phase 5b with super-agent fields (``plugin_id``, ``is_fast_path``,
``args``, ``arg_bindings``, ``parallel_groups``) so the graph can drive
multi-tool plans across plugins.
Agentic session memory and goal-loop fields added for the agentic playground chat.

``DynamicAPIState`` is composed from four sub-states (repo-polish phase 5) via
TypedDict multiple inheritance — this is a *typing-only* split. At runtime a
TypedDict is a plain ``dict``; ``StateGraph(DynamicAPIState)`` (``graph.py``),
the Postgres checkpointer, and every node's flat ``state.get("...")`` access
are completely unaffected, because ``TypedDict.__required_keys__``/
``__optional_keys__``/``get_type_hints(..., include_extras=True)`` all merge
across the base classes the same way a single flat class would declare them.
**Do not** nest a sub-state as a dict-valued field (``state["triage"]["mode"]``)
— that would break every flat accessor plus the ``messages``/``model_audit``
reducers below, and is not what this split does.

Sub-state boundaries (informal — purely organizational, no runtime meaning):
  - ``TriageSubState``: input + routing decisions (what kind of turn is this).
  - ``PlanExecutionSubState``: the GOAP plan + per-step execution mechanics.
  - ``WorkflowSubState``: YAML workflow fields, agentic session/goal-loop
    memory, and structured GOAP goal-fact tracking — all state that persists
    or accumulates *across* rounds/turns rather than within one step.
  - ``AuditSubState``: the conversation transcript + observability trail.
"""

import operator
from typing import Annotated, Literal

# typing.TypedDict does not set __orig_bases__ for a plain (non-generic)
# multiple-inheritance class on Python < 3.12 — typing_extensions backports
# that behavior, which test_dynamic_states_fields.py relies on to verify
# DynamicAPIState is actually composed from the four sub-states below
# (rather than redeclaring their fields flat).
from typing_extensions import TypedDict

from langgraph.graph.message import add_messages


class RouteCandidate(TypedDict, total=False):
    """A candidate route returned by pgvector top-k semantic search.

    Carries ``plugin_id`` so the executor can resolve auth + fast_path lookup
    through the central RouteRegistry, and ``is_fast_path`` so the dispatcher
    can short-circuit to a registered callable when one exists.
    """

    operation_id: str
    path: str
    path_template: str
    method: str
    description: str
    score: float
    parameters: dict
    plugin_id: str
    is_fast_path: bool


class ExecutionStep(TypedDict, total=False):
    """A single step in the planner's decomposed plan.

    ``arg_bindings`` references prior step outputs (e.g.
    ``{"record_id": "$steps[0].id"}``) — resolved by step_dispatcher_node
    just before each step executes.
    """

    operation_id: str
    plugin_id: str
    is_fast_path: bool
    intent: str
    args: dict
    arg_bindings: dict
    depends_on: int | None
    model: str | None
    for_each: str
    for_each_values: list
    for_each_limit: int | None
    kind: str                 # "wait" marks an async poll step (else a normal step)
    match: dict               # selector identifying the target record, e.g. {"id": "$steps[0].id"}
    until: dict               # completion predicate {"field": "status", "equals": "COMPLETED"}
    fail_on: list             # statuses that abort the poll, e.g. ["FAILED", "EXPIRED"]
    interval_s: int           # seconds between polls
    max_polls: int            # max poll attempts before timeout


class TriageSubState(TypedDict):
    """Input + routing decisions: what kind of turn this is and how to handle it."""

    # Input
    user_query: str
    # Stable original user question for the whole turn. Unlike `user_query` — which the
    # goal loop rewrites to a focused sub-directive on every replan (see goap_goal.py
    # continue paths) — this is stamped once per turn (reset_turn_fragment) and never
    # mutated, so summary_node can synthesize an answer to what was actually asked
    # instead of the last sub-task.
    original_query: str | None

    # Execution mode — bypass the confidence gate and execute directly.
    force_execute: bool

    # Scopes granted to the current MCP caller (from API key / JWT bearer).
    # None means "no auth context" (e.g. stdio/local) — scope checks are skipped.
    caller_scopes: list[str] | None

    # Triage (unified engine)
    triage_mode: str | None  # "chat" | "classic" | "specialist" | legacy "task" | None
    retriage_count: int  # per-turn retriage rounds (cap via graph.retriage_max)
    retriage_context: dict | None  # why retriage fired (from_stack, reason, remaining)

    # --- Decompose-first pipeline (decompose node, runs before embedder) ---
    # NOT written to `goal`: truthy `goal` flips agentic-mode gates (permission_gate,
    # validator, step_resolver), so decomposition output lives in its own keys.
    sub_tasks: list[str] | None      # NL sub-task queries driving per-intent embedding retrieval
    decompose_intent: str | None     # one-line intent extracted by the decompose pass
    decompose_seed_values: dict      # preliminary literal params extracted pre-retrieval


class PlanExecutionSubState(TypedDict):
    """The GOAP plan and per-step execution mechanics."""

    # Route candidates from pgvector (shared pool for entire plan)
    candidates: list[RouteCandidate]
    candidate_pool: list[RouteCandidate]

    # Multi-step plan (planner output)
    plan: list[ExecutionStep]
    current_step_index: int
    step_results: list[dict]
    last_results: list[dict] | None
    # Optional ordering hint: groups of step indices that may run in parallel.
    parallel_groups: list[list[int]]

    # Per-step execution state (reset between steps)
    selected: RouteCandidate | None
    confidence: float
    pgvector_score: float
    llm_confidence: float
    gate_decision: Literal["execute", "confirm", "clarify"]
    clarification_question: str | None
    clarify_questions: list[dict] | None
    payload: dict | None
    response: dict | None
    retry_count: int

    # Argument resolution & recovery context
    resolved_args: dict | None
    unresolved_required: list[str]
    retry_highlight: str | None

    # Replan context (dynamic replan with failure memory)
    replan_context: list[dict]
    last_failure: dict | None


class WorkflowSubState(TypedDict):
    """YAML workflow fields, agentic session/goal-loop memory, and GOAP goal-fact tracking.

    Everything here persists or accumulates *across* rounds/turns, unlike
    ``PlanExecutionSubState``'s per-step-within-one-plan fields.
    """

    # YAML-driven workflow planning fields (Phase 2)
    instruction_set: list[dict]
    workflow_name: str
    workflow_model: str
    yaml_workflow: str
    workflow_outputs: dict
    replan_count: int
    workflow_plan_id: str | None
    execution_id: str | None

    # --- Agentic session memory + goal-loop (playground) ---
    session_id: str | None        # checkpointer thread id for this chat session
    working_memory: dict          # resolved entities/facts carried across turns (e.g. {"record_id": 123})
    # Snapshot of working_memory *before* round_summary folds step_results this round.
    # Used by goap_goal's no-progress guard as the pre-fold baseline (topology is
    # step_dispatcher → round_summary → goap_goal). Transient; reset per turn.
    pre_round_memory: dict | None
    # Per-turn cross-round evidence log; appended by round_summary, read by goap_goal
    # verifier + final summary_node; reset by reset_turn_fragment.
    research_notes: list[dict] | None
    # Per-turn MinIO offload refs (short_id + kind + bytes); accumulated by
    # round_summary, surfaced on the final envelope; reset by reset_turn_fragment
    # but NOT cleared on goal-loop continue.
    artifacts: list[dict] | None
    last_summary: str | None      # rolling natural-language recap fed into the next turn
    goal: str | list[str] | None  # overarching user goal or goal facts for the autonomous loop
    iterations: int               # goal-loop iteration counter (this session/turn)
    max_iterations: int           # safety cap for the autonomous goal-loop
    repeat_failure_count: int     # count of consecutive identical failures
    no_progress_rounds: int       # count of consecutive verifier "continue" rounds with no new memory/ops (stall-breaker)
    last_plan_op_ids: list[str]   # list of operation_ids from the last executed plan
    goal_loop_decision: str | None  # "continue" | "done" | "retriage"

    # --- Structured GOAP goal tracking (goap_goal node) ---
    goal_facts: list[str]            # persistent structured goal facts (e.g. ["did:searchRecords", "have:record_id"])
    remaining_goal_facts: list[str]  # unmet sub-goals computed by goap_goal, fed forward to the planner
    achieved_facts: list[str]        # sub-goals satisfied so far (tracking/telemetry)
    executed_op_ids: list[str]       # cumulative operation_ids executed across loop iterations
    seed_values: dict                # literal param values extracted by the goal LLM (e.g. {"query": "John"})
    fanout_target: int | None        # target count for multi-fetch fan-out (e.g. "fetch 5 pages"), drives replan-for-remaining


class AuditSubState(TypedDict):
    """The conversation transcript and observability trail."""

    # LLM conversation turns
    messages: Annotated[list, add_messages]

    # Summary (post-execution natural-language recap)
    summary: str | None

    # Token tracking
    token_usage: dict

    # Spec-driven multi-model role selection audit trail (core_graph/model_roles/).
    # One entry per run_role_ladder call — which rung fired, on what model, why it
    # escalated. Reduced with operator.add (not last-write-wins): parallel_groups
    # runs steps concurrently and this is the only other reduced field besides
    # `messages`, so a plain list would silently drop concurrent entries.
    model_audit: Annotated[list, operator.add]


class DynamicAPIState(
    TriageSubState, PlanExecutionSubState, WorkflowSubState, AuditSubState
):
    """Full state for the dynamic graph pipeline — composed from the four sub-states above.

    Inheritance-only composition (see module docstring): this class declares
    no fields of its own, and at runtime is indistinguishable from a single
    flat TypedDict with all of the above fields merged.
    """
