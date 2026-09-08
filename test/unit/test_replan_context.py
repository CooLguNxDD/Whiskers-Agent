"""Acceptance tests for context-aware dynamic replan.

When a step yields an empty result that a downstream step depends on, or a
recoverable/transient error, the orchestrator must capture a structured
failure record, feed it forward so the planner can adapt (mutate args / pick
an alternate op), persist a fresh YAML per replan attempt, and stop with a
useful message once the replan budget is exhausted.

These tests pin the *module-level* helper contracts that the nested
``validator_node`` / ``planner_node`` closures delegate to (the closures
themselves are not importable, so the decision logic lives in pure helpers).
"""
import core_graph.graph as g
from core_graph.goap.goal_loop import reset_turn_fragment
from utils.server_config import MAX_REPLANS


# --- is_empty_result --------------------------------------------------------

def test_is_empty_result_bare_empty_list():
    assert g.is_empty_result([]) is True


def test_is_empty_result_wrapped_empty_list():
    assert g.is_empty_result({"status": "ok", "data": []}) is True


def test_is_empty_result_envelope_empty_results():
    # Notion-style search envelope with zero hits.
    assert g.is_empty_result({"results": []}) is True


def test_is_empty_result_nonempty_list():
    assert g.is_empty_result([{"id": 1}]) is False


def test_is_empty_result_wrapped_nonempty():
    assert g.is_empty_result({"status": "ok", "data": [{"id": 1}]}) is False


def test_is_empty_result_error_is_not_empty():
    # An error envelope is handled by the error path, not the empty path.
    assert g.is_empty_result({"status": "error", "message": "boom"}) is False


def test_is_empty_result_none():
    assert g.is_empty_result(None) is False


# --- _step_has_dependents ---------------------------------------------------

def test_step_has_dependents_true():
    state = {
        "current_step_index": 0,
        "plan": [
            {"operation_id": "search"},
            {"operation_id": "fetch", "arg_bindings": {"id": "$steps[0].id"}},
        ],
    }
    assert g._step_has_dependents(state) is True


def test_step_has_dependents_false_other_index():
    state = {
        "current_step_index": 0,
        "plan": [
            {"operation_id": "search"},
            {"operation_id": "other", "arg_bindings": {"id": "$steps[1].id"}},
        ],
    }
    assert g._step_has_dependents(state) is False


def test_step_has_dependents_no_bindings():
    state = {"current_step_index": 0, "plan": [{"operation_id": "search"}]}
    assert g._step_has_dependents(state) is False


# --- _build_failure_record --------------------------------------------------

def test_build_failure_record_error():
    state = {
        "current_step_index": 2,
        "selected": {"operation_id": "notion-fetch"},
        "resolved_args": {"page_id": "x"},
        "replan_context": [],
    }
    rec = g._build_failure_record(state, {"status": "error", "message": "fetch failed"}, "error")
    assert rec["operation_id"] == "notion-fetch"
    assert rec["outcome"] == "error"
    assert "fetch failed" in rec["detail"]
    assert rec["attempt"] == 1  # len(replan_context) + 1


def test_build_failure_record_empty_increments_attempt():
    state = {
        "current_step_index": 0,
        "selected": {"operation_id": "notion-search"},
        "resolved_args": {"query": "Open Cat Tunnel"},
        "replan_context": [{"attempt": 1}],
    }
    rec = g._build_failure_record(state, {"status": "ok", "data": []}, "empty")
    assert rec["operation_id"] == "notion-search"
    assert rec["outcome"] == "empty"
    assert rec["attempt"] == 2
    assert rec["args"] == {"query": "Open Cat Tunnel"}


# --- format_replan_context (prompt injection block) -------------------------

def test_format_replan_context_renders_attempts():
    recs = [{
        "attempt": 1, "operation_id": "notion-search",
        "args": {"query": "Open Cat Tunnel"}, "outcome": "empty",
        "detail": "search returned 0 results",
    }]
    out = g.format_replan_context(recs)
    assert "notion-search" in out
    assert "Open Cat Tunnel" in out
    assert "0 results" in out or "empty" in out


def test_format_replan_context_empty_is_blank():
    assert g.format_replan_context([]) == ""
    assert g.format_replan_context(None) == ""


# --- build_replan_reset (the state fragment validator_node returns) ---------

def test_build_replan_reset_preserves_context_and_clears_plan_id():
    state = {
        "replan_count": 0,
        "replan_context": [{"attempt": 1, "operation_id": "old"}],
        "current_step_index": 1,
        "selected": {"operation_id": "notion-search"},
        "resolved_args": {"query": "Open Cat Tunnel"},
        "workflow_plan_id": "abc-123",
        "step_results": [{"status": "ok"}],
        "plan": [
            {"operation_id": "notion-search"},
            {"operation_id": "notion-fetch"},
        ],
    }
    out = g.build_replan_reset(state, {"status": "ok", "data": []}, "empty")

    assert out["response"] == {"status": "replan"}
    assert out["plan"] == []
    assert out["yaml_workflow"] == ""
    assert out["workflow_plan_id"] is None          # forces fresh YAML persist
    assert out["replan_count"] == 1
    assert out["current_step_index"] == 0
    assert out["step_results"] == []
    assert out["retry_count"] == 0
    # Failure memory must survive the reset and accumulate.
    assert len(out["replan_context"]) == 2
    assert out["replan_context"][-1]["operation_id"] == "notion-search"
    assert out["last_failure"]["outcome"] == "empty"
    # Identical-plan detection needs last_plan_op_ids on validator replan
    assert out["last_plan_op_ids"] == ["notion-search", "notion-fetch"]
    assert out["last_results"] == [{"status": "ok"}]


def test_build_replan_reset_strips_failed_id_seeds():
    state = {
        "replan_count": 0,
        "replan_context": [],
        "selected": {"operation_id": "jules_plugin__julesget_session"},
        "resolved_args": {"sessionId": "Jules"},
        "seed_values": {"sessionId": "Jules", "query": "keep-me"},
        "working_memory": {"sessionId": "Jules", "query": "keep-me"},
        "plan": [{"operation_id": "jules_plugin__julesget_session"}],
        "step_results": [],
    }
    out = g.build_replan_reset(
        state,
        {"status": "error", "message": "404 not found", "http_status": 404},
        "error",
    )
    assert "sessionId" not in (out.get("seed_values") or {})
    assert "sessionId" not in (out.get("working_memory") or {})
    assert out["seed_values"].get("query") == "keep-me"
    assert out["working_memory"].get("query") == "keep-me"
    assert out["last_plan_op_ids"] == ["jules_plugin__julesget_session"]


def test_format_replan_context_includes_identical_plan_feedback():
    recs = [{
        "plan": [
            {"operation_id": "julesget_session"},
            {"operation_id": "juleslist_sessions"},
        ],
        "feedback": "This exact sequence of operations was just attempted and resulted in failure.",
    }]
    out = g.format_replan_context(recs)
    assert "identical-plan feedback" in out
    assert "julesget_session" in out
    assert "MUST" in out or "failure" in out


# --- should_replan_on_empty (routing gate) ----------------------------------

def test_should_replan_on_empty_true_when_dependent_and_budget():
    state = {
        "current_step_index": 0,
        "replan_count": 0,
        "plan": [
            {"operation_id": "search"},
            {"operation_id": "fetch", "arg_bindings": {"id": "$steps[0].id"}},
        ],
    }
    assert g.should_replan_on_empty(state, {"status": "ok", "data": []}) is True


def test_should_replan_on_empty_false_without_dependents():
    state = {
        "current_step_index": 0,
        "replan_count": 0,
        "plan": [{"operation_id": "list_records"}],
    }
    assert g.should_replan_on_empty(state, {"status": "ok", "data": []}) is False


def test_should_replan_on_empty_false_when_budget_exhausted():
    state = {
        "current_step_index": 0,
        "replan_count": MAX_REPLANS,
        "plan": [
            {"operation_id": "search"},
            {"operation_id": "fetch", "arg_bindings": {"id": "$steps[0].id"}},
        ],
    }
    assert g.should_replan_on_empty(state, {"status": "ok", "data": []}) is False


def test_should_replan_on_empty_false_for_nonempty():
    state = {
        "current_step_index": 0,
        "replan_count": 0,
        "plan": [
            {"operation_id": "search"},
            {"operation_id": "fetch", "arg_bindings": {"id": "$steps[0].id"}},
        ],
    }
    assert g.should_replan_on_empty(state, {"status": "ok", "data": [{"id": 1}]}) is False


# --- transient error now replan-eligible ------------------------------------

def test_request_failed_is_recoverable():
    """A transient 'fetch failed' (request_failed) must be replan-eligible."""
    assert g.is_recoverable({"status": "error", "error": "request_failed",
                             "message": "GET ...: fetch failed"}) is True


def test_404_is_recoverable():
    """A 404 is frequently the symptom of a GOAP-guessed/garbage id arg
    (e.g. an unresolved by-id chain) rather than a genuinely missing
    resource — it must be replan-eligible, not immediately terminal."""
    assert g.is_recoverable({"status": "error", "http_status": 404}) is True


# --- per-turn reset seeds fresh failure memory ------------------------------

def test_reset_turn_fragment_inits_replan_fields():
    out = reset_turn_fragment({"working_memory": {}})
    assert out["replan_context"] == []
    assert out["last_failure"] is None


# --- budget bumped ----------------------------------------------------------

def test_max_replans_default_bumped():
    assert MAX_REPLANS >= 2
