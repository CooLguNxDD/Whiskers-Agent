"""Confidence gate, retry/replan routing decisions, and empty-result detection.
Split out of core_graph/node/helpers.py (Phase 4 modularity refactor)."""
import re

from core_graph.states import DynamicAPIState
from core_graph.node.helpers._shared import _RESULT_ENVELOPE_KEYS, _BINDING_RE
from utils.server_config import MAX_REPLANS

try:
    from utils.server_config import (
        LONG_CHAIN_THRESHOLD,
        CONFIDENCE_EXECUTE_THRESHOLD,
        CONFIDENCE_CONFIRM_THRESHOLD,
    )
except (ImportError, AttributeError):
    LONG_CHAIN_THRESHOLD = 3
    CONFIDENCE_EXECUTE_THRESHOLD = 0.70
    CONFIDENCE_CONFIRM_THRESHOLD = 0.50


def compute_gate(
    pgvector_score: float,
    llm_score: float | None,
    step_count: int = 1,
) -> tuple[float, str]:
    """Weighted confidence → gate decision.

    Long chains always require confirmation regardless of confidence —
    a super-agent shouldn't fire a long plan unattended.
    """
    confidence = pgvector_score * 0.6 + (llm_score or pgvector_score) * 0.4
    if step_count > LONG_CHAIN_THRESHOLD:
        return confidence, "confirm"
    if confidence > CONFIDENCE_EXECUTE_THRESHOLD:
        return confidence, "execute"
    if confidence >= CONFIDENCE_CONFIRM_THRESHOLD:
        return confidence, "confirm"
    return confidence, "clarify"


def is_recoverable(response: dict) -> bool:
    """400 / 404 / 422 responses and missing required parameter errors are potentially fixable on retry.

    404 is included because a not-found is frequently the symptom of a
    GOAP-guessed/garbage id argument (e.g. an unresolved by-id chain) rather
    than a genuinely missing resource — routing it through the replan path
    lets the planner rebind the argument instead of hard-halting immediately.
    """
    if not isinstance(response, dict):
        return False
    if response.get("http_status", 0) in (400, 404, 422):
        return True
    status = response.get("status")
    if status == "need_input":
        return True
    err = response.get("error")
    if err in ("request_failed", "pipeline_error"):
        return True
    message = str(response.get("message") or "").lower()
    if "missing" in message or "required" in message or "typeerror" in message or "undefined" in message:
        return True
    if "fetch failed" in message:
        return True
    return False


def _normalize_param_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def detect_failed_params(message: str, known_params) -> list:
    """Match a structured API error message (e.g. "Invalid value at 'session.source_context'")
    back to the top-level param name(s) that produced it, so a retry can target the
    actual culprit instead of blindly resubmitting the same malformed value."""
    if not message:
        return []
    norm_msg = _normalize_param_name(message)
    hits = []
    for p in known_params:
        norm_p = _normalize_param_name(p)
        if norm_p and norm_p in norm_msg:
            hits.append(p)
    return hits


def is_empty_result(response) -> bool:
    """Checks if a given operation response is semantically empty (e.g. empty list or 404/empty message)."""
    if response is None:
        return False
    if isinstance(response, list):
        return len(response) == 0
    if isinstance(response, dict):
        status = response.get("status")
        if status is not None and status not in ("ok", None):
            return False
        for env in _RESULT_ENVELOPE_KEYS:
            if env in response:
                val = response[env]
                if isinstance(val, list):
                    return len(val) == 0
        if len(response) == 0:
            return True
        for v in response.values():
            if isinstance(v, list) and len(v) == 0:
                return True
        return False
    return False


def _step_has_dependents(state) -> bool:
    idx = state.get("current_step_index", 0)
    plan = state.get("plan") or []
    for j in range(idx + 1, len(plan)):
        step = plan[j]
        bindings = step.get("arg_bindings") or {}
        for v in bindings.values():
            if isinstance(v, str):
                m = _BINDING_RE.match(v.strip())
                if m and int(m.group(1)) == idx:
                    return True
    return False


def should_replan_on_empty(state, response) -> bool:
    """Determines if a replan should be triggered based on an empty result and current replan count."""
    return (
        is_empty_result(response)
        and _step_has_dependents(state)
        and state.get("replan_count", 0) < MAX_REPLANS
    )


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
