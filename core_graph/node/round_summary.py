"""Per-round deterministic accumulator for the goal loop.

Runs every iteration after the step dispatcher finishes a plan, before
``goap_goal``. Captures bounded research notes + layout into state so
cross-round evidence survives ``step_results`` resets on continue. No LLM.
Final natural-language synthesis is owned by ``summary_node`` on the done path.

Also runs fail-safe MinIO artifact offload for large string fields so the
envelope stays slim while full bodies remain retrievable via session-gated
REST / MCP tools.
"""

import json
import logging

from core_graph.goap.goal_loop import append_research_note, fold_working_memory
from core_graph.node.artifact_offload import offload_step_results_for_state
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.summary import (
    _coerce_layout_candidate,
    _extract_layout_from_step_results,
)
from core_graph.states import DynamicAPIState

logger = logging.getLogger("whiskers")


def _ops_from_plan(plan) -> list[str]:
    """Extract operation_ids from the just-executed plan steps."""
    ops: list[str] = []
    for step in plan or []:
        if not isinstance(step, dict):
            continue
        op = step.get("operation_id")
        if isinstance(op, str) and op:
            ops.append(op)
    return ops


def _short_last_summary(step_results: list, iteration: int, ops: list[str]) -> str:
    """Deterministic one-liner fallback for goap_goal.get_terminal_response."""
    if ops:
        ops_s = ", ".join(ops[:5])
        if len(ops) > 5:
            ops_s += f" (+{len(ops) - 5})"
        base = f"Round {iteration}: {ops_s}"
    else:
        base = f"Round {iteration} complete."
    # Project to op/status only — never fully serialize large step payloads.
    try:
        slim = [
            {"op": r.get("operation_id"), "status": r.get("status")}
            if isinstance(r, dict)
            else {"status": "unknown"}
            for r in (step_results or [])
        ]
        blob = json.dumps(slim, default=str)
    except (TypeError, ValueError):
        blob = ""
    if blob and blob not in ("[]", "{}"):
        snippet = blob[:120].replace("\n", " ")
        return f"{base} | {snippet}"
    return base


def make_round_summary_node(_ctx: GraphRuntimeContext):
    """Create the per-round deterministic accumulator node (no LLM)."""

    async def round_summary_node(state: DynamicAPIState) -> dict:
        """Append a research note, fold ids/layout into working_memory, set last_summary."""
        step_results = state.get("step_results") or []
        plan = state.get("plan") or []
        ops = _ops_from_plan(plan)
        # Align with the iteration count goap_goal will stamp next (+1).
        iteration = int(state.get("iterations") or 0) + 1
        directive = state.get("user_query") or ""

        # Snapshot memory *before* fold so goap_goal's no-progress guard can
        # detect real growth (topology is round_summary → goap_goal; without
        # this baseline, goap_goal only sees post-fold memory).
        pre_round_memory = dict(state.get("working_memory") or {})

        # MinIO offload before notes/fold; also rebinds response.data to slim results.
        step_results, artifacts, slim_response = await offload_step_results_for_state(
            state, step_results=step_results
        )

        notes = append_research_note(
            state.get("research_notes"),
            iteration=iteration,
            directive=directive,
            ops=ops,
            step_results=step_results,
        )

        working_memory = fold_working_memory(
            pre_round_memory,
            step_results,
        )
        # Preserve mid-loop emit_layout / design_layout across step_results resets.
        layout = _extract_layout_from_step_results(step_results)
        if layout is None:
            layout = _coerce_layout_candidate(working_memory.get("layout"))
        if layout is not None:
            working_memory = {**working_memory, "layout": layout}

        last_summary = _short_last_summary(step_results, iteration, ops)

        out: dict = {
            "research_notes": notes,
            "working_memory": working_memory,
            "last_summary": last_summary,
            "pre_round_memory": pre_round_memory,
            "step_results": step_results,
            "artifacts": artifacts,
        }
        # Keep finalize envelope in sync with slimmed step_results.
        if slim_response is not None:
            out["response"] = slim_response
        return out

    return round_summary_node
