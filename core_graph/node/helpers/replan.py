"""Replan failure-record building and replan-context formatting.
Split out of core_graph/node/helpers.py (Phase 4 modularity refactor)."""

import logging

logger = logging.getLogger("whiskers")


def _build_failure_record(state, response, outcome) -> dict:
    plan = state.get("plan") or []
    plan_ops = [s.get("operation_id") for s in plan if isinstance(s, dict) and s.get("operation_id")]
    return {
        "attempt": len(state.get("replan_context") or []) + 1,
        "operation_id": (state.get("selected") or {}).get("operation_id", ""),
        "args": dict(state.get("resolved_args") or {}),
        "outcome": outcome,
        "detail": (
            "search returned 0 results"
            if outcome == "empty"
            else str(response.get("message") or response.get("error") or "error")
        ),
        # Full plan sequence so identical-plan detection / LLM can see what failed
        "plan_ops": plan_ops,
    }


def format_replan_context(records) -> str:
    """Formats historical replan failure records into a context string for the LLM."""
    if not records:
        return ""
    lines = ["Previous failed attempts (do NOT repeat — adapt):"]
    for r in records:
        if not isinstance(r, dict):
            continue
        # Identical-plan feedback records use {plan, feedback}
        feedback = r.get("feedback")
        if feedback:
            plan = r.get("plan") or []
            ops = [
                s.get("operation_id")
                for s in plan
                if isinstance(s, dict) and s.get("operation_id")
            ] or r.get("plan_ops") or []
            ops_s = " → ".join(ops) if ops else "(unknown sequence)"
            lines.append(f"- identical-plan feedback: {ops_s} — {feedback}")
            continue
        ops = r.get("plan_ops") or []
        ops_s = f" plan=[{', '.join(ops)}]" if ops else ""
        lines.append(
            f"- attempt {r.get('attempt')}: {r.get('operation_id')} "
            f"args={r.get('args')}{ops_s} -> {r.get('outcome')}: {r.get('detail')}"
        )
    return "\n".join(lines)


def build_replan_reset(state, response, outcome) -> dict:
    """Builds a state update dictionary for triggering a replan after a failed step.

    Sets last_plan_op_ids so the planner's identical-plan retry can fire, strips
    failed id-like args from seed_values/working_memory so GOAP cannot re-satisfy
    have:{param} with the same garbage binding, and preserves step_results as
    last_results for downstream context.
    """
    record = _build_failure_record(state, response, outcome)
    new_ctx = list(state.get("replan_context") or []) + [record]
    replan_count = state.get("replan_count", 0)

    plan = state.get("plan") or []
    last_plan_op_ids = [
        s.get("operation_id") for s in plan if isinstance(s, dict) and s.get("operation_id")
    ]

    failed_args = dict(state.get("resolved_args") or {})
    # Also merge args from the failure record if resolved_args empty
    if not failed_args and isinstance(record.get("args"), dict):
        failed_args = dict(record["args"])

    from core_graph.goap.integrate.seed_hygiene import strip_failed_seed_keys

    new_seeds, new_mem = strip_failed_seed_keys(
        state.get("seed_values"),
        state.get("working_memory"),
        failed_args,
    )

    step_results = list(state.get("step_results") or [])

    # Best-effort durable anti-pattern (fire-and-forget; replan path is sync).
    try:
        from core_graph.harness.episode import record_anti_pattern_sync_fire_and_forget

        record_anti_pattern_sync_fire_and_forget(
            {
                **state,
                "last_failure": record,
                "plan": plan,
            },
            outcome=str(outcome or "error"),
            detail=str(record.get("detail") or ""),
            plan_ops=list(last_plan_op_ids),
        )
    except Exception:
        # Anti-pattern recording is best-effort; replan must proceed.
        logger.debug("replan: anti-pattern record failed", exc_info=True)

    return {
        "replan_count": replan_count + 1,
        "plan": [],
        "yaml_workflow": "",
        "payload": None,
        "response": {"status": "replan"},
        "current_step_index": 0,
        "step_results": [],
        "last_results": step_results,
        "retry_count": 0,
        "replan_context": new_ctx,
        "last_failure": record,
        "last_plan_op_ids": last_plan_op_ids,
        "workflow_plan_id": None,
        # Decompose-first: force a fresh decomposition on the replan path.
        "sub_tasks": None,
        "seed_values": new_seeds,
        "working_memory": new_mem,
    }
