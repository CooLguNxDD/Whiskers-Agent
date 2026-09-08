"""
Routing node utilities.
"""
from core_graph.states import DynamicAPIState
from core_graph.node.helpers import is_recoverable

def retry_router(state: DynamicAPIState) -> str:
    """Evaluate execution response to choose: next_step, retry, replan, or halt."""
    response = state.get("response")
    retry_count = state.get("retry_count", 0)
    if not isinstance(response, dict):
        return "halt"
    status = response.get("status")
    if status == "replan":
        return "replan"
    if status == "auth_required":
        return "halt"
    if status == "need_input":
        return "halt"
    if is_recoverable(response) and retry_count < 1:
        return "retry"
    if status == "error" and state.get("goal"):
        return "recover"
    if status == "error":
        return "halt"
    return "next_step"

def triage_router(state: DynamicAPIState) -> str:
    """Route after triage: chat | classic | specialist (task ≡ classic)."""
    from core_graph.node.triage import normalize_triage_mode

    mode = normalize_triage_mode(state.get("triage_mode"))
    if mode == "chat":
        return "chat"
    if mode == "specialist":
        return "specialist"
    return "classic"


def specialist_entry_router(state: DynamicAPIState) -> str:
    """After specialist_entry: done (summary) when pipeline stamped response, else classic plan."""
    resp = state.get("response")
    if isinstance(resp, dict) and resp.get("status") in ("ok", "partial", "error"):
        # Pipeline produced a terminal envelope (including discover-only).
        if resp.get("specialist") or resp.get("layout") is not None or resp.get("goal_class"):
            return "done"
    return "plan"


def goal_check_router(state: DynamicAPIState) -> str:
    """Route goal-loop decision: continue | done | retriage."""
    decision = state.get("goal_loop_decision", "done")
    if decision == "retriage":
        return "retriage"
    if decision == "continue":
        return "continue"
    return "done"

def gate_router(state: DynamicAPIState) -> str:
    """Route after planner based on confidence gate."""
    if state.get("response"):
        return "done"
    if state.get("force_execute", False):
        return "context_check"
    decision = state.get("gate_decision", "clarify")
    if decision == "execute":
        return "context_check"
    if decision == "confirm":
        return "confirm_node"
    return "clarify_node"

def confirm_router(state: DynamicAPIState) -> str:
    """After confirm: elicitation-accept clears ``response`` + forces execute →
    proceed; otherwise (confirmation_needed / halted) end the call."""
    if state.get("response"):
        return "done"
    return "execute"

def clarify_router(state: DynamicAPIState) -> str:
    """After clarify: an elicited answer clears ``response`` → re-plan with the
    refined query; otherwise (clarification_needed / halted) end the call."""
    if state.get("response"):
        return "done"
    return "replan"

def more_steps_router(state: DynamicAPIState) -> str:
    """Routes execution either to context check for the next step, summary on completion, or done.

    Parallel-group partial failures recover via goap_goal when a goal is set so
    sibling successes are folded into memory and the planner can replan — not
    bare END (which dropped list_sessions results after a peer get failed).
    """
    plan = state.get("plan") or []
    idx = state.get("current_step_index", 0)
    response = state.get("response") or {}
    # Finalizing (all steps done or partial)
    if response.get("steps_executed") is not None or response.get("status") == "partial":
        if response.get("status") == "ok":
            return "summary"
        # partial / non-ok finalize: prefer goal-loop recovery over hard halt
        if response.get("status") == "partial" and state.get("goal"):
            return "recover"
        return "done"
    if idx < len(plan) and state.get("selected"):
        return "context_check"
    return "done"


# The goap_goal node reuses goal_check_router (keys on goal_loop_decision).
goap_goal_router = goal_check_router


def context_resolution_router(state: DynamicAPIState) -> str:
    """
    Route after context_check: builder if all required resolved, else step_resolver.
    """
    # Fan-out steps bind their id-like param per item ($item) inside the batch
    # executor; their "unresolved" param is expected, so skip the resolver/need_input.
    plan = state.get("plan") or []
    idx = state.get("current_step_index", 0)
    cur_step = plan[idx] if 0 <= idx < len(plan) else {}
    if cur_step.get("for_each") or cur_step.get("for_each_values"):
        return "builder"
    if state.get("unresolved_required"):
        return "step_resolver"
    return "builder"

def step_resolver_router(state: DynamicAPIState) -> str:
    """
    Route after step_resolver: end if it produced a response (need_input/confirm), else builder.
    """
    response = state.get("response")
    if response:
        if isinstance(response, dict) and response.get("status") == "need_input" and state.get("goal"):
            return "goap_goal"
        return "done"
    return "builder"


def permission_gate_router(state: DynamicAPIState) -> str:
    """Route after permission_gate: if it emitted a terminal response (error/confirm) then done, else builder."""
    if state.get("response"):
        return "done"
    return "builder"


def builder_router(state):
    """Route after builder: wait steps go to the poll node, all others to the executor.
    A builder-emitted response (need_input/error) always passes through the executor."""
    if state.get("response"):
        return "executor"
    plan = state.get("plan") or []
    idx = state.get("current_step_index", 0)
    step = plan[idx] if 0 <= idx < len(plan) else {}
    if step.get("kind") == "wait":
        return "wait_node"
    return "executor"

