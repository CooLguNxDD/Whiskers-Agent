"""Unwrap CatPortfolio Ask run_graph wrappers; classify from the visitor sentence."""

import json

from plugins.portfolio_plugin.ask.visitor_turn import (
    fallback_answer_markdown,
    is_flow_debug_summary,
    parse_visitor_turn,
    visitor_plugin_context,
)

_PAGE = {
    "view": "tank",
    "block_index": [
        {"id": "h1", "type": "hero"},
        {"id": "fish-tank-1", "type": "fishTank"},
    ],
    "tank_slugs": ["helix-ai", "helix-devops"],
    "dag": {"levels": [{"level": 0, "nodes": ["h1"]}]},
    "time_span": None,
    "add_slugs": ["fisoul"],
    "visitor_session_id": "sess-visitor",
}


def _wrap(question: str, *, goal_class: str = "scoped_ask") -> str:
    return (
        "You are a helpful portfolio assistant chatting with a visitor on Andrew's "
        "portfolio website.\nYour primary task is to answer visitor questions using "
        "the available portfolio tools.\n\n---\n\n"
        f"Visitor Question: {question}\n\n"
        f"[System: CatPortfolio ask turn (goal_class={goal_class}). "
        "Route via portfolio_ask_v1: route_portfolio_ask -> build_ask_overlay. "
        "Return changed blocks only; do not compose or bake a whole page unless "
        "the visitor is actually describing a job posting. "
        f"Page context: {json.dumps(_PAGE)}]"
    )


def test_unwraps_game_question_as_scoped_ask():
    turn = parse_visitor_turn(_wrap("tell me about your game project"))
    assert turn is not None
    assert turn.question == "tell me about your game project"
    assert turn.goal_class == "scoped_ask"
    assert turn.view == "tank"
    assert turn.tank_slugs == ["helix-ai", "helix-devops"]
    assert turn.block_index[0]["id"] == "h1"
    assert turn.dag["levels"][0]["nodes"] == ["h1"]


def test_bake_question_ignores_wrapper_scoped_ask():
    turn = parse_visitor_turn(
        _wrap("bake a portfolio for an AI Developer role at DummyAI Labs")
    )
    assert turn is not None
    assert turn.goal_class == "bake_for_job"
    assert "DummyAI" in turn.question


def test_bare_playground_query_is_not_a_visitor_turn():
    assert parse_visitor_turn("tell me about your game project") is None
    assert parse_visitor_turn("list jules sessions") is None


def test_plugin_context_seeds_flow_board_slots():
    turn = parse_visitor_turn(_wrap("tell me about Whiskers Agent"))
    ctx = visitor_plugin_context(turn)
    assert ctx["question"] == "tell me about Whiskers Agent"
    assert ctx["tank_slugs"] == ["helix-ai", "helix-devops"]
    assert ctx["block_index"][1]["type"] == "fishTank"
    assert ctx["add_slugs"] == ["fisoul"]
    assert ctx["visitor_session_id"] == "sess-visitor"


def test_fallback_answer_keeps_real_markdown_and_replaces_flow_debug():
    assert is_flow_debug_summary("Flow 'portfolio_ask_v1' completed (3 stage(s)).")
    kept = fallback_answer_markdown(
        question="tell me about devops",
        highlight_slugs=["helix-devops"],
        answer_markdown="Helix EKS is the production cluster.",
    )
    assert kept.startswith("Helix EKS")
    filled = fallback_answer_markdown(
        question="tell me about devops",
        highlight_slugs=["helix-devops", "helix-ai"],
        answer_markdown="Flow 'portfolio_ask_v1' completed (3 stage(s)).",
    )
    assert "helix-devops" in filled
    assert "Flow '" not in filled


def test_fallback_answer_prefers_recommendation_names_over_highlights():
    filled = fallback_answer_markdown(
        question="quantum?",
        highlight_slugs=["helix-ai"],
        recommendations=[
            {"slug": "helix-ai", "name": "Helix AI", "in_tank": True},
            {"slug": "fisoul", "name": "Fisoul", "in_tank": False},
        ],
        answer_markdown="",
    )
    assert "Helix AI" in filled
    assert "Fisoul" in filled
    assert "Not a direct match" in filled


def test_fallback_answer_prefers_fish_pool_over_recommendations():
    """A non-empty fish pool is spawnable, so it wins over the plain
    in-tank recommendation wording — and points the visitor at the
    ``spawn_pooled_fish`` follow-up phrase."""
    filled = fallback_answer_markdown(
        question="quantum?",
        highlight_slugs=["helix-ai"],
        recommendations=[{"slug": "helix-ai", "name": "Helix AI", "in_tank": True}],
        pool=[{"slug": "fisoul", "name": "Fisoul"}],
        answer_markdown="",
    )
    assert "fisoul" in filled
    assert "add them to the tank" in filled
    assert "Not a direct match" not in filled


def test_select_flow_uses_classified_goal_class():
    from core_graph.subgraphs.specialist.flow_registry import clear_flows, select_flow
    from plugins.portfolio_plugin.tests.test_portfolio_ask_flow import _load_flows

    clear_flows()
    try:
        _load_flows()
        ask = parse_visitor_turn(_wrap("tell me about your game project"))
        bake = parse_visitor_turn(
            _wrap("bake a portfolio for an AI Developer role at DummyAI Labs")
        )
        assert select_flow(ask.question, goal_class=ask.goal_class).flow_id == "portfolio_ask_v1"
        assert select_flow(bake.question, goal_class=bake.goal_class).flow_id == "portfolio_bake_v1"
    finally:
        clear_flows()
