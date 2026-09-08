"""Ask overlay: changed blocks only, stable bands, frozen chronology."""

import pytest

from plugins.portfolio_plugin.ask.overlay import build_ask_overlay
from plugins.portfolio_plugin.ask.router import AskPlan

INDEX = [
    {"id": "h1", "type": "hero"},
    {"id": "card-helix-ai", "type": "card"},
    {"id": "fish-tank-1", "type": "fishTank"},
    {"id": "cta-floor", "type": "quickActions"},
]

DAG = {
    "levels": [
        {"level": 0, "label": "Intro", "nodes": ["h1"], "at": 0.0},
        {"level": 2, "label": "Projects", "nodes": ["card-helix-ai"], "cols": 2, "at": 0.5},
        {"level": 3, "label": "Architecture", "nodes": ["fish-tank-1"], "at": 0.75},
        {"level": 7, "label": "Ask", "nodes": ["cta-floor"], "at": 1.0},
    ]
}

PROJECTS = [
    {
        "slug": "helix-ai",
        "name": "Helix AI",
        "summary": "LangGraph agent orchestration with retrieval and evals.",
        "tags": ["ai", "langgraph"],
        "links": [{"label": "repo", "href": "https://example.com/ai"}],
        "started_on": "2024-01-01",
        "ended_on": "2024-12-31",
    },
    {
        "slug": "helix-devops",
        "name": "Helix DevOps",
        "summary": "Kubernetes CI/CD pipelines, terraform infra, observability.",
        "tags": ["devops", "kubernetes"],
        "links": [{"label": "repo", "href": "https://example.com/devops"}],
        "started_on": "2020-01-01",
        "ended_on": "2020-12-31",
    },
]


@pytest.fixture(autouse=True)
def _stub_projects(monkeypatch):
    async def _list_projects(*_a, **_k):
        return list(PROJECTS)

    monkeypatch.setattr("plugins.portfolio_plugin.store.list_projects", _list_projects)


def _depths(block):
    return {f["slug"]: f["depth"] for f in block["props"]["fish"]}


async def test_focus_returns_only_the_tank_block():
    plan = AskPlan(
        intent="focus_fish",
        focus_slug="helix-ai",
        target_ids=("fish-tank-1",),
        highlight_slugs=("helix-ai",),
    )
    out = await build_ask_overlay(
        plan,
        question="tell me about the ai work",
        block_index=INDEX,
        tank_slugs=["helix-ai", "helix-devops"],
        dag=DAG,
    )
    assert out["status"] == "ok"
    assert [b["id"] for b in out["blocks"]] == ["fish-tank-1"]
    assert out["patched_block_ids"] == ["fish-tank-1"]
    assert out["focus_slug"] == "helix-ai"


async def test_focus_fish_never_grows_the_roster():
    """focus_fish is about a fish already in the tank — the rebuilt block may
    re-score the dossier blurb, but its specimen slug set must exactly match
    tank_slugs (no respawn), even if a plan carried a stray add_slugs entry.
    """
    plan = AskPlan(
        intent="focus_fish",
        focus_slug="helix-ai",
        add_slugs=("helix-ai",),  # stray — must be filtered, not respawned
        target_ids=("fish-tank-1",),
        highlight_slugs=("helix-ai",),
    )
    out = await build_ask_overlay(
        plan,
        question="tell me about the ai work",
        block_index=INDEX,
        tank_slugs=["helix-ai", "helix-devops"],
        dag=DAG,
    )
    assert out["status"] == "ok"
    tank_block = next(b for b in out["blocks"] if b["id"] == "fish-tank-1")
    assert {f["slug"] for f in tank_block["props"]["fish"]} == {"helix-ai", "helix-devops"}


async def test_untouched_blocks_keep_their_bands():
    plan = AskPlan(intent="focus_fish", target_ids=("fish-tank-1",))
    out = await build_ask_overlay(
        plan,
        question="ai",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        dag=DAG,
    )
    nodes = {lvl["label"]: lvl["nodes"] for lvl in out["dag"]["levels"]}
    assert nodes["Intro"] == ["h1"]
    assert nodes["Projects"] == ["card-helix-ai"]
    assert nodes["Ask"] == ["cta-floor"]


async def test_frozen_time_span_keeps_existing_depths(monkeypatch):
    """M2: adding a dated project must not re-depth the school."""
    base = AskPlan(intent="focus_fish", target_ids=("fish-tank-1",))
    before = await build_ask_overlay(
        base, question="ai", block_index=INDEX, tank_slugs=["helix-ai"], dag=DAG
    )
    span = before["blocks"][0]["props"].get("timeSpan")

    add = AskPlan(
        intent="add_fish",
        focus_slug="helix-devops",
        add_slugs=("helix-devops",),
        target_ids=("fish-tank-1",),
    )
    after = await build_ask_overlay(
        add,
        question="kubernetes",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        dag=DAG,
        time_span=span,
    )
    assert "helix-devops" in _depths(after["blocks"][0])
    if span is not None:
        assert _depths(after["blocks"][0])["helix-ai"] == _depths(before["blocks"][0])["helix-ai"]


async def test_unfrozen_span_is_the_rebake_behaviour():
    """Without a frozen span the scale is re-derived — the bake path's contract."""
    add = AskPlan(
        intent="add_fish", add_slugs=("helix-devops",), target_ids=("fish-tank-1",)
    )
    out = await build_ask_overlay(
        add,
        question="kubernetes",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        dag=DAG,
        time_span=None,
    )
    span = out["blocks"][0]["props"].get("timeSpan")
    assert span is not None and span["min"] < span["max"]


async def test_highlight_slugs_survive_the_overlay():
    """P2 regression: highlightSlugs used to be dropped by model_dump."""
    plan = AskPlan(
        intent="focus_fish",
        target_ids=("fish-tank-1",),
        highlight_slugs=("helix-ai",),
    )
    out = await build_ask_overlay(
        plan, question="ai", block_index=INDEX, tank_slugs=["helix-ai"], dag=DAG
    )
    assert out["highlight_slugs"] == ["helix-ai"]
    assert out["blocks"][0]["props"]["highlightSlugs"] == ["helix-ai"]


async def test_focus_never_repopulates_the_tank():
    """A focus turn keeps the roster it was given — not the whole inventory."""
    plan = AskPlan(intent="focus_fish", target_ids=("fish-tank-1",))
    out = await build_ask_overlay(
        plan, question="ai", block_index=INDEX, tank_slugs=["helix-ai"], dag=DAG
    )
    assert set(_depths(out["blocks"][0])) == {"helix-ai"}


async def test_text_patch_also_spawns_the_fish_when_tank_is_empty(monkeypatch):
    async def _card(*_a, **_k):
        return {
            "status": "ok",
            "block": {
                "type": "card",
                "id": "card-helix-ai",
                "props": {"title": "Helix AI", "summary": "agents"},
            },
        }

    monkeypatch.setattr(
        "plugins.portfolio_plugin.compose.block_builder.build_layout_block_impl",
        _card,
    )
    plan = AskPlan(
        intent="patch_blocks",
        focus_slug="helix-ai",
        add_slugs=("helix-ai",),
        block_steps=(
            {
                "block_type": "card",
                "block_id": "card-helix-ai",
                "slugs": ["helix-ai"],
                "query": "ai project",
            },
        ),
        target_ids=("card-helix-ai",),
    )
    out = await build_ask_overlay(
        plan,
        question="tell me about your ai project",
        block_index=INDEX,
        tank_slugs=[],
        dag=DAG,
    )
    assert out["status"] == "ok"
    types = [b["type"] for b in out["blocks"]]
    assert "fishTank" in types
    tank = next(b for b in out["blocks"] if b["type"] == "fishTank")
    assert any(f["slug"] == "helix-ai" for f in tank["props"]["fish"])


async def test_answer_only_ships_no_blocks_but_is_not_a_failure():
    out = await build_ask_overlay(
        AskPlan(intent="answer_only", focus_slug="helix-ai"),
        question="anything",
        block_index=INDEX,
        dag=DAG,
    )
    assert out["status"] == "ok"
    assert out["blocks"] == []
    assert out["dag"] == DAG
    assert out["recommendations"] == []


async def test_missing_tank_block_degrades_without_raising():
    plan = AskPlan(intent="focus_fish", target_ids=("fish-tank-1",))
    out = await build_ask_overlay(
        plan,
        question="ai",
        block_index=[{"id": "h1", "type": "hero"}],
        tank_slugs=["helix-ai"],
        dag=None,
    )
    assert out["status"] == "ok"
    assert out["blocks"] == []
    assert any("no tank block" in w for w in out["warnings"])


async def test_list_projects_failure_surfaces_as_error_not_ok(monkeypatch):
    """A DB fetch failure is not a legitimate empty patch — must not be
    silently coerced into an answer-only ``status: ok`` turn."""

    async def _boom(*_a, **_k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("plugins.portfolio_plugin.store.list_projects", _boom)
    plan = AskPlan(intent="focus_fish", target_ids=("fish-tank-1",))
    out = await build_ask_overlay(
        plan,
        question="ai",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        dag=DAG,
    )
    assert out["status"] == "error"
    assert out["blocks"] == []
    assert any("inventory unavailable" in e for e in out["errors"])
    assert out["recommendations"] == []


async def test_ungrounded_add_is_reported_not_invented():
    plan = AskPlan(
        intent="add_fish", add_slugs=("ghost-project",), target_ids=("fish-tank-1",)
    )
    out = await build_ask_overlay(
        plan,
        question="ghost",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        dag=DAG,
    )
    slugs = _depths(out["blocks"][0]) if out["blocks"] else {}
    assert "ghost-project" not in slugs
    assert any("ghost-project" in w for w in out["warnings"])
    assert out["recommendations"] == []


_REC = {
    "slug": "helix-ai",
    "name": "Helix AI",
    "blurb": "agents",
    "tags": ["ai"],
    "reason": "closest tag match",
    "in_tank": True,
}


async def test_recommend_intent_is_ok_with_empty_blocks_and_chips():
    plan = AskPlan(intent="recommend", recommendations=(_REC,))
    out = await build_ask_overlay(
        plan, question="quantum?", block_index=INDEX, tank_slugs=["helix-ai"], dag=DAG
    )
    assert out["status"] == "ok"
    assert out["blocks"] == []
    assert out["recommendations"] == [_REC]


async def test_recommendations_present_on_all_status_branches(monkeypatch):
    recs = (_REC,)
    ok = await build_ask_overlay(
        AskPlan(intent="answer_only", recommendations=recs),
        question="q",
        block_index=INDEX,
        dag=DAG,
    )
    assert ok["status"] == "ok" and ok["recommendations"] == [_REC]

    async def _boom(*_a, **_k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("plugins.portfolio_plugin.store.list_projects", _boom)
    err = await build_ask_overlay(
        AskPlan(intent="focus_fish", target_ids=("fish-tank-1",), recommendations=recs),
        question="ai",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        dag=DAG,
    )
    assert err["status"] == "error" and err["recommendations"] == [_REC]


async def test_fish_pool_excludes_in_tank_recommendations_and_carries_pool_id():
    """``fish_pool`` is the not-in-tank subset of ``recommendations`` — the
    client redeems it via ``spawn_pooled_fish(pool_id, slugs)``."""
    not_in_tank = {**_REC, "slug": "helix-devops", "in_tank": False}
    plan = AskPlan(
        intent="recommend",
        recommendations=(_REC, not_in_tank),
        pool_id="pool-xyz",
    )
    out = await build_ask_overlay(
        plan, question="quantum?", block_index=INDEX, tank_slugs=["helix-ai"], dag=DAG
    )
    assert out["status"] == "ok"
    assert [r["slug"] for r in out["fish_pool"]] == ["helix-devops"]
    assert out["pool_id"] == "pool-xyz"


async def test_patch_blocks_spawn_copies_recommendations_through():
    plan = AskPlan(
        intent="patch_blocks",
        focus_slug="helix-ai",
        add_slugs=("helix-ai",),
        block_steps=(),
        target_ids=("card-helix-ai",),
        recommendations=(_REC,),
    )
    out = await build_ask_overlay(
        plan,
        question="ai",
        block_index=INDEX,
        tank_slugs=[],
        dag=DAG,
    )
    assert out["recommendations"] == [_REC]

