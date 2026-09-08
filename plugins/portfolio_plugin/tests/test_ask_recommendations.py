"""Recommendation chips: rich dicts on the plan, add_slugs short-circuit, fish-pool stash/take."""

from __future__ import annotations

import pytest

from plugins.portfolio_plugin.ask import fish_pool as fp
from plugins.portfolio_plugin.ask import router as ask_router
from plugins.portfolio_plugin.ask.router import AskPlan, plan_from_dict, route_ask

INDEX = [
    {"id": "h1", "type": "hero"},
    {"id": "fish-tank-1", "type": "fishTank"},
]

PROJECTS = [
    {
        "slug": "helix-devops",
        "name": "Helix DevOps",
        "summary": "Kubernetes CI/CD pipelines.",
        "tags": ["devops"],
    },
    {
        "slug": "helix-ai",
        "name": "Helix AI",
        "summary": "LangGraph agent orchestration.",
        "tags": ["ai"],
    },
    {
        "slug": "fisoul",
        "name": "Fisoul",
        "summary": "Unity multiplayer game.",
        "tags": ["game"],
    },
]


@pytest.fixture(autouse=True)
def _stub_backends(monkeypatch):
    async def _list_projects(*_a, **_k):
        return list(PROJECTS)

    async def _rank(projects, query, **_k):
        return list(projects)

    monkeypatch.setattr("plugins.portfolio_plugin.store.list_projects", _list_projects)
    monkeypatch.setattr(
        "plugins.portfolio_plugin.compose.ranking.rank_projects_by_query", _rank
    )
    monkeypatch.setattr(ask_router, "_ask_settings", lambda: {"confidence_floor": 0.0})
    fp.reset_pools()
    yield
    fp.reset_pools()


async def test_add_slugs_short_circuits_to_add_fish():
    plan = await route_ask(
        "anything",
        view="tank",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        add_slugs=["fisoul"],
    )
    assert plan.intent == "add_fish"
    assert plan.add_slugs == ("fisoul",)
    assert plan.focus_slug == "fisoul"


async def test_unknown_add_slug_is_rejected_not_spawned():
    plan = await route_ask(
        "add ghost",
        view="tank",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        add_slugs=["ghost-project"],
    )
    assert plan.intent == "answer_only"
    assert plan.add_slugs == ()


async def test_add_slugs_accepts_pooled_slug_not_in_inventory(monkeypatch):
    async def _empty(*_a, **_k):
        return []

    monkeypatch.setattr("plugins.portfolio_plugin.store.list_projects", _empty)
    fp.stash_pool("sess-add", [{"slug": "virtual-fish", "name": "Virtual", "blurb": "pooled"}])
    plan = await route_ask(
        "add it",
        view="tank",
        block_index=INDEX,
        tank_slugs=[],
        add_slugs=["virtual-fish"],
        visitor_session_id="sess-add",
    )
    assert plan.intent == "add_fish"
    assert plan.add_slugs == ("virtual-fish",)


async def test_recommendations_survive_to_dict_plan_from_dict_roundtrip():
    rec = {
        "slug": "helix-ai",
        "name": "Helix AI",
        "blurb": "agents",
        "tags": ["ai"],
        "reason": "closest tag match",
        "in_tank": True,
    }
    plan = AskPlan(
        intent="recommend",
        recommend_slugs=("helix-ai",),
        pool_slugs=("fisoul",),
        recommendations=(rec,),
        reason="nearest matches",
    )
    d = plan.to_dict()
    assert d["recommendations"] == [rec]
    roundtripped = plan_from_dict(d)
    assert roundtripped.recommendations == (rec,)
    assert roundtripped.recommend_slugs == ("helix-ai",)
    assert roundtripped.pool_slugs == ("fisoul",)


async def test_discover_branch_stashes_recommendations_on_the_plan(monkeypatch):
    monkeypatch.setattr(
        ask_router,
        "_ask_settings",
        lambda: {"confidence_floor": 99.0, "discovery_fallback": True},
    )
    plan = await route_ask(
        "quantum photonics?",
        view="tank",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        visitor_session_id="sess-disc",
    )
    assert plan.intent == "discover"
    assert plan.recommendations
    slugs = {str(r["slug"]) for r in plan.recommendations}
    assert "helix-ai" in slugs
    taken = fp.take_from_session(
        "sess-disc", [r["slug"] for r in plan.recommendations if not r.get("in_tank")]
    )
    assert taken, "pool recs must be stashed for a later add-chip turn"


async def test_stash_then_take_across_two_turns(monkeypatch):
    """First real caller coverage: recommend stashes; add_slugs takes from the pool."""
    monkeypatch.setattr(
        ask_router,
        "_ask_settings",
        lambda: {"confidence_floor": 99.0, "discovery_fallback": False},
    )
    first = await route_ask(
        "quantum photonics?",
        view="tank",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        visitor_session_id="sess-two",
    )
    assert first.intent == "recommend"
    pool_slug = next(r["slug"] for r in first.recommendations if not r.get("in_tank"))
    second = await route_ask(
        f"Add {pool_slug} to the tank",
        view="tank",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        add_slugs=[pool_slug],
        visitor_session_id="sess-two",
    )
    assert second.intent == "add_fish"
    assert pool_slug in second.add_slugs


def test_take_from_session_stash_then_take():
    fp.stash_pool("sess-turns", [{"slug": "fisoul", "name": "Fisoul", "blurb": "game"}])
    taken = fp.take_from_session("sess-turns", ["fisoul"])
    assert len(taken) == 1
    assert taken[0]["slug"] == "fisoul"
    again = fp.take_from_session("sess-turns", ["fisoul"])
    assert again[0]["slug"] == "fisoul"


def test_take_from_session_unknown_or_empty():
    assert fp.take_from_session("nope", ["fisoul"]) == []
    assert fp.take_from_session("", ["fisoul"]) == []
    fp.stash_pool("sess-empty", [{"slug": "fisoul"}])
    assert fp.take_from_session("sess-empty", []) == []


def test_stash_replaces_prior_pool_for_same_session():
    first = fp.stash_pool("sess-replace", [{"slug": "old-fish"}])
    second = fp.stash_pool("sess-replace", [{"slug": "new-fish"}])
    assert fp.get_pool(first) == {}
    assert [p["slug"] for p in fp.get_pool(second)["projects"]] == ["new-fish"]
    assert fp.take_from_session("sess-replace", ["old-fish"]) == []
    assert fp.take_from_session("sess-replace", ["new-fish"])[0]["slug"] == "new-fish"

