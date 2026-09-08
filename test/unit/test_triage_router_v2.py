"""Triage v2 routing: chat | classic | specialist (+ task alias)."""

from __future__ import annotations

from core_graph.node.routers import goap_goal_router, specialist_entry_router, triage_router
from core_graph.node.triage import normalize_triage_mode, _heuristic_triage
from core_graph.runtime.retriage import build_retriage_reset, should_retriage


def test_normalize_task_to_classic():
    assert normalize_triage_mode("task") == "classic"
    assert normalize_triage_mode("TASK") == "classic"
    assert normalize_triage_mode("specialist") == "specialist"
    assert normalize_triage_mode(None) == "classic"


def test_triage_router_modes():
    assert triage_router({"triage_mode": "chat"}) == "chat"
    assert triage_router({"triage_mode": "classic"}) == "classic"
    assert triage_router({"triage_mode": "specialist"}) == "specialist"
    assert triage_router({"triage_mode": "task"}) == "classic"
    assert triage_router({}) == "classic"


def test_specialist_entry_router():
    assert specialist_entry_router({}) == "plan"


def test_goap_goal_router_retriage():
    assert goap_goal_router({"goal_loop_decision": "retriage"}) == "retriage"
    assert goap_goal_router({"goal_loop_decision": "continue"}) == "continue"
    assert goap_goal_router({"goal_loop_decision": "done"}) == "done"
    assert goap_goal_router({}) == "done"


def test_heuristic_portfolio():
    assert _heuristic_triage("redesign my portfolio layout") == "specialist"
    assert _heuristic_triage("catportfolio ask turn (goal_class=scoped_ask)") == "specialist"
    assert _heuristic_triage("list jules sessions") == "classic"


def test_should_retriage_portfolio_on_classic():
    state = {
        "triage_mode": "classic",
        "original_query": "bake portfolio for this job",
        "replan_count": 2,
        "retriage_count": 0,
    }
    assert should_retriage(state) is True


def test_should_retriage_respects_cap():
    state = {
        "triage_mode": "classic",
        "original_query": "bake portfolio for this job",
        "replan_count": 5,
        "retriage_count": 2,
    }
    assert should_retriage(state) is False


def test_build_retriage_reset_preserves_count():
    delta = build_retriage_reset(
        {
            "triage_mode": "classic",
            "retriage_count": 0,
            "original_query": "x",
            "working_memory": {"a": 1},
        }
    )
    assert delta["retriage_count"] == 1
    assert delta["goal_loop_decision"] is None
    assert delta["plan"] == []
