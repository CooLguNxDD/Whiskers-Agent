from core_graph.goap.planner import GoapPlan
from core_graph.goap.integrate.fanout import (
    _strip_op_prefix,
    _inject_defensive_fanout,
    _normalize_item_bindings,
)


def goap_steps_to_state(
    goap_plan: GoapPlan,
    candidates: list[dict],
    fan_out: dict | None = None,
    working_memory: dict | None = None,
    limit: int | None = None,
) -> dict:
    """
    Convert a GoapPlan into a state fragment compatible with DynamicAPIState.
    """
    steps = goap_plan.steps
    if not steps:
        raise ValueError("goap_plan.steps must not be empty")

    first_step_op_id = steps[0].get("operation_id")
    selected_candidate = None
    for cand in candidates:
        if cand.get("operation_id") == first_step_op_id:
            selected_candidate = cand
            break

    if selected_candidate is None:
        selected_candidate = candidates[0] if candidates else {}
        if steps:
            logger = __import__("logging").getLogger("whiskers")
            logger.warning(
                "goap_steps_to_state: first step op_id %r had no exact match in candidates; "
                "fell back to candidates[0] for 'selected'. This can occur with proxy names "
                "containing hyphens/case (e.g. proxy_Notion-AndrewDev-2). Execution guards + "
                "defensive fill should still deliver seed literals via the step op_id path.",
                steps[0].get("operation_id"),
            )

    applied_fanout = False
    if fan_out:
        op_id = fan_out.get("operation")
        over = fan_out.get("over")
        if op_id and over:
            if op_id.startswith("did:"):
                op_id = op_id[4:]
            op_id_stripped = _strip_op_prefix(op_id)
            for step in steps:
                step_op = step.get("operation_id") or ""
                # Match raw, or proxy-prefix-stripped on both sides — the LLM emits
                # bare op names (notion-fetch) while plan steps carry the proxy
                # prefix (proxy_Notion-AndrewDev-2__notion-fetch).
                if step_op == op_id or _strip_op_prefix(step_op) == op_id_stripped:
                    if isinstance(over, str) and over.startswith("$steps["):
                        step["for_each"] = over
                    elif isinstance(over, list):
                        step["for_each_values"] = over
                    else:
                        step["for_each_values"] = [over]
                    if isinstance(limit, int) and limit > 0:
                        step["for_each_limit"] = limit
                    applied_fanout = True

    # Defensive: inject for_each for search→fetch chains even when the LLM produced a
    # fan_out descriptor that didn't match any step (common with proxy op names), and
    # seed for_each_values from working_memory plural id lists / pending re-fetch queue.
    if not applied_fanout:
        _inject_defensive_fanout(steps, working_memory, limit=limit)

    # Ensure any $steps-based for_each step binds per item (handles the LLM fan_out
    # path, which sets for_each but leaves producer-step bindings intact).
    for step in steps:
        _normalize_item_bindings(step)

    return {
        "plan": steps,
        "instruction_set": steps,
        "parallel_groups": [],
        "current_step_index": 0,
        "step_results": [],
        "selected": selected_candidate,
        "workflow_name": "goap-plan",
        "workflow_outputs": {},
    }
