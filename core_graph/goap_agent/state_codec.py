"""Serialize / deserialize DynamicAPIState for MCP JSON payloads."""

from __future__ import annotations

import json
from typing import Any
import logging
logger = logging.getLogger("whiskers")


# Keys that are large, non-JSON, or unsafe to echo back to MCP clients.
_REDACT_KEYS = frozenset({"messages"})
_MAX_STR = 8000
_MAX_LIST = 50
_MAX_DEPTH = 8


def initial_state(
    user_query: str,
    *,
    force_execute: bool = False,
    session_id: str | None = None,
    caller_scopes: list[str] | None = None,
    caller_role: str | None = None,
    caller_kind: Any = None,
) -> dict[str, Any]:
    """Build a minimal DynamicAPIState seed matching run_graph_impl."""
    return {
        "user_query": user_query,
        "candidates": [],
        "candidate_pool": [],
        "plan": [],
        "current_step_index": 0,
        "step_results": [],
        "last_results": None,
        "parallel_groups": [],
        "selected": None,
        "confidence": 0.0,
        "pgvector_score": 0.0,
        "llm_confidence": 0.0,
        "gate_decision": "execute",
        "clarification_question": None,
        "clarify_questions": None,
        "payload": None,
        "response": None,
        "retry_count": 0,
        "force_execute": force_execute,
        "caller_scopes": caller_scopes,
        "caller_role": caller_role,
        "caller_kind": caller_kind,
        "resolved_args": None,
        "unresolved_required": [],
        "retry_highlight": None,
        "replan_context": [],
        "last_failure": None,
        "messages": [],
        "instruction_set": [],
        "workflow_name": "",
        "workflow_model": "",
        "yaml_workflow": "",
        "workflow_outputs": {},
        "replan_count": 0,
        "workflow_plan_id": None,
        "execution_id": None,
        "triage_mode": None,
        "retriage_count": 0,
        "retriage_context": None,
        "summary": None,
        "token_usage": {},
        "session_id": session_id,
        "working_memory": {},
        "last_summary": None,
        "goal": None,
        "iterations": 0,
        "max_iterations": 20,
        "repeat_failure_count": 0,
        "last_plan_op_ids": [],
        "goal_loop_decision": None,
        "goal_facts": [],
        "remaining_goal_facts": [],
        "achieved_facts": [],
        "executed_op_ids": [],
        "seed_values": {},
        "fanout_target": None,
        "sub_tasks": None,
        "decompose_intent": None,
        "decompose_seed_values": {},
    }


def merge_delta(state: dict[str, Any], delta: dict[str, Any] | None) -> dict[str, Any]:
    """Merge a node return dict into session state (shallow keys)."""
    if not delta:
        return state
    out = dict(state)
    for key, value in delta.items():
        out[key] = value
    return out


def to_public_snapshot(state: dict[str, Any], *, include_messages: bool = False) -> dict[str, Any]:
    """Return a JSON-safe pruned snapshot of graph state."""
    return _sanitize(state, depth=0, include_messages=include_messages)


def _sanitize(value: Any, *, depth: int, include_messages: bool) -> Any:
    if depth > _MAX_DEPTH:
        return "<max_depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_STR:
            return value[:_MAX_STR] + f"...(+{len(value) - _MAX_STR})"
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not include_messages and k in _REDACT_KEYS:
                out[k] = f"<{type(v).__name__} redacted>"
                continue
            try:
                out[str(k)] = _sanitize(v, depth=depth + 1, include_messages=include_messages)
            except Exception:
                out[str(k)] = f"<unserializable {type(v).__name__}>"
        return out
    if isinstance(value, (list, tuple)):
        items = list(value)[:_MAX_LIST]
        sanitized = [_sanitize(v, depth=depth + 1, include_messages=include_messages) for v in items]
        if len(value) > _MAX_LIST:
            sanitized.append(f"...(+{len(value) - _MAX_LIST} more)")
        return sanitized
    # LangChain messages and other objects
    content = getattr(value, "content", None)
    if content is not None:
        role = getattr(value, "type", None) or value.__class__.__name__
        return {"type": str(role), "content": _sanitize(content, depth=depth + 1, include_messages=include_messages)}
    if hasattr(value, "value"):  # Enum
        try:
            return value.value
        except Exception:
            logger.debug("state_codec.py: swallowed exception", exc_info=True)
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)
