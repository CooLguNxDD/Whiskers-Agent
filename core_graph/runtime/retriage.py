"""Retriage helpers — goal-miss re-entry to shared triage."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("whiskers")


def retriage_max() -> int:
    """Max retriage rounds per turn (default 2)."""
    try:
        from utils.server_config import get_config_registry

        cfg = get_config_registry()
        server = cfg.get("server") if hasattr(cfg, "get") else None
        if not isinstance(server, dict):
            # ConfigRegistry may expose raw dict via .data / [] 
            server = getattr(cfg, "data", None) or {}
        graph = (server or {}).get("graph") if isinstance(server, dict) else {}
        if isinstance(graph, dict) and graph.get("retriage_max") is not None:
            return int(graph["retriage_max"])
    except Exception:
        # Config unavailable — keep hard default.
        logger.debug("retriage: retriage_max config read failed", exc_info=True)
    return 2


def should_retriage(state: dict[str, Any] | None) -> bool:
    """Return True when the goal loop should re-enter triage instead of same-stack replan.

    Conservative signals (PR3):
    - explicit force_retriage
    - high replan_count with portfolio-shaped query while triage was classic
    - retriage_count under cap
    """
    if not state:
        return False
    count = int(state.get("retriage_count") or 0)
    if count >= retriage_max():
        return False
    if state.get("force_retriage"):
        return True

    replan_count = int(state.get("replan_count") or 0)
    mode = (state.get("triage_mode") or "classic").lower()
    if mode == "task":
        mode = "classic"
    q = (state.get("original_query") or state.get("user_query") or "").lower()
    portfolioish = any(
        k in q
        for k in (
            "portfolio",
            "layout",
            "bake",
            "design_layout",
            "compose_scoped",
            "genui",
        )
    )
    # Classic stack thrashing on a portfolio-shaped goal → retriage to specialist
    if mode == "classic" and portfolioish and replan_count >= 2:
        return True
    # Specialist path that fell through to classic planning and still fails
    if mode == "specialist" and replan_count >= 3:
        return True
    # Explicit reason from specialist pipeline / last_failure
    rc = state.get("retriage_context") if isinstance(state.get("retriage_context"), dict) else {}
    if rc.get("reason") in ("specialist_compose_failed", "specialist_pipeline_failed"):
        return count < retriage_max()
    lf = state.get("last_failure") if isinstance(state.get("last_failure"), dict) else {}
    if lf.get("error") in ("specialist_pipeline_failed", "specialist_compose_failed"):
        return count < retriage_max()
    return False


def build_retriage_reset(state: dict[str, Any] | None) -> dict[str, Any]:
    """Partial state reset for retriage; preserves original_query and memory."""
    if not state:
        return {}
    count = int(state.get("retriage_count") or 0) + 1
    from_stack = state.get("triage_mode") or "classic"
    return {
        "retriage_count": count,
        "retriage_context": {
            "reason": "goal_loop_retriage",
            "from_stack": from_stack,
            "remaining_goal_facts": list(state.get("remaining_goal_facts") or [])[:20],
            "replan_count": state.get("replan_count"),
        },
        "force_retriage": False,
        "force_execute": False,
        "plan": [],
        "current_step_index": 0,
        "step_results": [],
        "selected": None,
        "payload": None,
        "response": None,
        "goal_loop_decision": None,
        "retry_count": 0,
        # Keep original_query, working_memory, research_notes, session_id, callers
    }
