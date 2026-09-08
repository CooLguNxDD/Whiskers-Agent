"""Ask-mode discover spawn: virtual fish from indexed candidates, no DB write."""

import logging
import time

import pytest

from plugins.portfolio_plugin.ask import discovery_jobs as dj
from plugins.portfolio_plugin.ask.overlay import _build_tank_block, build_ask_overlay
from plugins.portfolio_plugin.ask.router import AskPlan
from plugins.portfolio_plugin.ask.spawn import spawn_projects_from_candidates
from plugins.portfolio_plugin.compose.quality import (
    filter_projects_for_layout,
    is_portfolio_worthy_project,
)
from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout

INDEX = [
    {"id": "h1", "type": "hero"},
    {"id": "fish-tank-1", "type": "fishTank"},
]

FROZEN_SPAN = {"min": 2020.0, "max": 2024.0}

CANDIDATES = [
    {
        "slug": "fisoul",
        "name": "Fisoul",
        "summary": "GitHub repository CooLguNxDD/Fisoul",
        "ref": "CooLguNxDD/Fisoul",
        "source": "github",
        "score": 1.2,
    }
]


@pytest.fixture(autouse=True)
def _reset_jobs():
    dj.reset_jobs()
    yield
    dj.reset_jobs()


@pytest.fixture(autouse=True)
def _stub_inventory(monkeypatch):
    async def _list_projects(*_a, **_k):
        return [
            {
                "slug": "helix-ai",
                "name": "Helix AI",
                "summary": "LangGraph agent orchestration with retrieval and evals.",
                "tags": ["ai"],
                "links": [{"label": "repo", "href": "https://example.com/ai"}],
                "started_on": "2022-01-01",
                "ended_on": "2023-01-01",
            }
        ]

    monkeypatch.setattr("plugins.portfolio_plugin.store.list_projects", _list_projects)


def test_spawned_candidate_clears_the_worthy_gate():
    spawned = spawn_projects_from_candidates(CANDIDATES)
    assert len(spawned) == 1
    assert spawned[0]["virtual"] is True
    assert "discovered" in spawned[0]["tags"]
    assert spawned[0]["links"][0]["href"] == "https://github.com/CooLguNxDD/Fisoul"
    assert is_portfolio_worthy_project(spawned[0])
    kept = filter_projects_for_layout(spawned)
    assert [p["slug"] for p in kept] == ["fisoul"]


async def test_extra_projects_join_the_tank_without_a_missing_row_warning():
    plan = AskPlan(
        intent="add_fish",
        focus_slug="fisoul",
        add_slugs=("fisoul",),
        target_ids=("fish-tank-1",),
        highlight_slugs=("fisoul",),
    )
    extra = spawn_projects_from_candidates(CANDIDATES)
    block, errors, fatal = await _build_tank_block(
        plan,
        question="tell me about your game",
        tenant_id=1,
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        time_span=FROZEN_SPAN,
        extra_projects=extra,
    )
    assert not fatal
    assert block is not None
    slugs = {f["slug"] for f in block["props"]["fish"]}
    assert "fisoul" in slugs
    assert not any("no active project row for 'fisoul'" in e for e in errors)


async def test_spawned_tank_validates_and_omits_null_years():
    extra = spawn_projects_from_candidates(CANDIDATES)
    plan = AskPlan(
        intent="add_fish",
        add_slugs=("fisoul",),
        target_ids=("fish-tank-1",),
        highlight_slugs=("fisoul",),
    )
    out = await build_ask_overlay(
        plan,
        question="game",
        block_index=INDEX,
        tank_slugs=[],
        extra_projects=extra,
        time_span=FROZEN_SPAN,
    )
    assert out["status"] == "ok"
    block = out["blocks"][0]
    specimen = next(f for f in block["props"]["fish"] if f["slug"] == "fisoul")
    assert "startYear" not in specimen
    assert "endYear" not in specimen
    layout = {
        "version": 1,
        "meta": {"audience": "default", "generatedAt": "t"},
        "blocks": [block],
    }
    parsed, errs = validate_layout(layout)
    assert not errs, errs
    assert parsed is not None
    dumped = next(f for f in parsed["blocks"][0]["props"]["fish"] if f["slug"] == "fisoul")
    assert "startYear" not in dumped
    assert "endYear" not in dumped


async def test_frozen_time_span_survives_an_undated_spawn():
    extra = spawn_projects_from_candidates(CANDIDATES)
    plan = AskPlan(intent="add_fish", add_slugs=("fisoul",), target_ids=("fish-tank-1",))
    out = await build_ask_overlay(
        plan,
        question="game",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        extra_projects=extra,
        time_span=FROZEN_SPAN,
    )
    assert out["blocks"][0]["props"]["timeSpan"] == FROZEN_SPAN


async def test_run_job_indexes_but_never_writes_projects(monkeypatch):
    captured: dict = {}

    async def _fake_run_discovery(**kwargs):
        captured.update(kwargs)
        return {"docs": []}

    monkeypatch.setattr(
        "plugins.portfolio_plugin.discovery.pipeline.run_discovery",
        _fake_run_discovery,
    )
    monkeypatch.setattr(
        dj,
        "_ask_settings",
        lambda: {
            "discovery_fallback": True,
            "discovery_index": True,
            "discovery_budget_s": 5,
        },
    )
    dj._jobs["j1"] = {
        "status": "pending",
        "query": "game",
        "created_at": time.time(),
        "projects": [],
        "error": "",
    }
    await dj._run_job("j1", "game", 1)
    assert captured["dry_run"] is True
    assert captured["write_back"] is False
    assert captured["do_index"] is True
    assert captured["index_on_dry_run"] is True


async def test_await_unknown_job_is_unknown_not_a_raise():
    out = await dj.await_discovery("does-not-exist")
    assert out["status"] == "unknown"
    assert out["projects"] == []


async def test_await_timeout_leaves_the_task_running():
    import asyncio

    started = asyncio.Event()

    async def _slow():
        started.set()
        await asyncio.sleep(30)

    job_id = "slow-1"
    dj._jobs[job_id] = {
        "status": "pending",
        "query": "game",
        "created_at": time.time(),
        "projects": [],
        "error": "",
    }
    task = asyncio.create_task(_slow())
    dj._tasks[job_id] = task
    await started.wait()

    out = await dj.await_discovery(job_id, timeout_s=0.05)
    assert out["status"] == "pending"
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_await_task_failure_logs_and_returns_error_record(caplog):
    import asyncio

    async def _fail():
        raise RuntimeError("discovery exploded")

    job_id = "failed-1"
    dj._jobs[job_id] = {
        "status": "error",
        "query": "game",
        "created_at": time.time(),
        "projects": [],
        "error": "discovery exploded",
    }
    dj._tasks[job_id] = asyncio.create_task(_fail())
    with caplog.at_level(logging.WARNING, logger=dj.logger.name):
        out = await dj.await_discovery(job_id, timeout_s=1)
    assert out["status"] == "error"
    assert out["error"] == "discovery exploded"
    assert "failed while awaiting" in caplog.text


async def test_extra_project_replaces_a_thin_real_row_with_the_same_slug(monkeypatch):
    """A real DB row for the slug exists but is a bare placeholder (no
    links/metrics/sources) — it would fail the worthy gate and, before this
    fix, shadowed the spawned candidate for the same slug (which spawn.py
    guarantees is worthy), so the fish silently vanished. The richer extra
    must win."""

    async def _list_projects(*_a, **_k):
        return [
            {
                "slug": "reversi-mcts",
                "name": "reversi-mcts",
                "summary": "",  # empty shell — fails is_portfolio_worthy_project
                "tags": [],
                "links": [],
                "metrics": [],
                "context_sources": [],
            }
        ]

    monkeypatch.setattr("plugins.portfolio_plugin.store.list_projects", _list_projects)

    plan = AskPlan(
        intent="add_fish",
        focus_slug="reversi-mcts",
        add_slugs=("reversi-mcts",),
        target_ids=("fish-tank-1",),
        highlight_slugs=("reversi-mcts",),
    )
    extra = spawn_projects_from_candidates(
        [
            {
                "slug": "reversi-mcts",
                "name": "reversi-mcts",
                "ref": "CooLguNxDD/reversi-mcts",
                "source": "github",
            }
        ]
    )
    block, errors, fatal = await _build_tank_block(
        plan,
        question="tell me about reversi",
        tenant_id=1,
        block_index=INDEX,
        tank_slugs=[],
        time_span=FROZEN_SPAN,
        extra_projects=extra,
    )
    assert not fatal
    assert block is not None
    slugs = {f["slug"] for f in block["props"]["fish"]}
    assert "reversi-mcts" in slugs


async def test_discover_turn_spawns_a_tank_block(monkeypatch):
    from plugins.portfolio_plugin.MCPTools import ask_tools

    monkeypatch.setattr(ask_tools, "_ask_enabled", lambda: True)

    async def _await(job_id, **_k):
        return {
            "status": "ready",
            "job_id": job_id,
            "query": "game",
            "projects": CANDIDATES,
            "error": "",
        }

    monkeypatch.setattr(
        ask_tools,
        "start_discovery",
        lambda *_a, **_k: {"status": "pending", "job_id": "j1", "query": "game"},
    )
    monkeypatch.setattr(ask_tools, "await_discovery", _await)

    out = await ask_tools.build_ask_overlay(
        ask_plan={"intent": "discover"},
        question="tell me about your game project",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        time_span=FROZEN_SPAN,
    )
    assert out["status"] == "ok"
    assert [b["type"] for b in out["blocks"]] == ["fishTank"]
    slugs = {f["slug"] for f in out["blocks"][0]["props"]["fish"]}
    assert "fisoul" in slugs
    assert out.get("pending_job") is None


async def test_discover_turn_spawns_every_matched_candidate(monkeypatch):
    """A multi-project question ('AI and DevOps projects') must spawn all of
    them in one turn, not just the top-scored candidate."""
    from plugins.portfolio_plugin.MCPTools import ask_tools

    monkeypatch.setattr(ask_tools, "_ask_enabled", lambda: True)

    candidates = [
        {
            "slug": "fisoul",
            "name": "Fisoul",
            "summary": "GitHub repository CooLguNxDD/Fisoul",
            "ref": "CooLguNxDD/Fisoul",
            "source": "github",
            "score": 1.2,
        },
        {
            "slug": "pullfrog",
            "name": "Pullfrog",
            "summary": "GitHub repository CooLguNxDD/pullfrog",
            "ref": "CooLguNxDD/pullfrog",
            "source": "github",
            "score": 1.1,
        },
    ]

    async def _await(job_id, **_k):
        return {
            "status": "ready",
            "job_id": job_id,
            "query": "game and devops",
            "projects": candidates,
            "error": "",
        }

    monkeypatch.setattr(
        ask_tools,
        "start_discovery",
        lambda *_a, **_k: {"status": "pending", "job_id": "j1", "query": "game and devops"},
    )
    monkeypatch.setattr(ask_tools, "await_discovery", _await)

    out = await ask_tools.build_ask_overlay(
        ask_plan={"intent": "discover"},
        question="show me the game and devops projects",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        time_span=FROZEN_SPAN,
    )
    assert out["status"] == "ok"
    slugs = {f["slug"] for f in out["blocks"][0]["props"]["fish"]}
    assert "fisoul" in slugs
    assert "pullfrog" in slugs
    assert out.get("pending_job") is None


async def test_discover_turn_skips_candidates_already_in_the_tank(monkeypatch):
    """Batch spawn checks against the existing roster: a candidate slug
    already swimming in the tank is not re-added, and doesn't eat the spawn
    cap that a genuinely missing candidate needs."""
    from plugins.portfolio_plugin.MCPTools import ask_tools

    monkeypatch.setattr(ask_tools, "_ask_enabled", lambda: True)

    candidates = [
        {
            "slug": "helix-ai",  # already in tank_slugs below
            "name": "Helix AI",
            "ref": "CooLguNxDD/helix-ai",
            "source": "github",
            "score": 1.3,
        },
        {
            "slug": "fisoul",  # genuinely missing
            "name": "Fisoul",
            "ref": "CooLguNxDD/Fisoul",
            "source": "github",
            "score": 1.2,
        },
    ]

    async def _await(job_id, **_k):
        return {
            "status": "ready",
            "job_id": job_id,
            "query": "ai stuff",
            "projects": candidates,
            "error": "",
        }

    monkeypatch.setattr(
        ask_tools,
        "start_discovery",
        lambda *_a, **_k: {"status": "pending", "job_id": "j1", "query": "ai stuff"},
    )
    monkeypatch.setattr(ask_tools, "await_discovery", _await)

    out = await ask_tools.build_ask_overlay(
        ask_plan={"intent": "discover"},
        question="show me the ai project again",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        time_span=FROZEN_SPAN,
    )
    assert out["status"] == "ok"
    fish_slugs = {f["slug"] for f in out["blocks"][0]["props"]["fish"]}
    assert fish_slugs == {"helix-ai", "fisoul"}
    # highlight pills cover both the already-present and the newly spawned
    assert set(out["highlight_slugs"]) == {"helix-ai", "fisoul"}


async def test_discover_turn_all_candidates_already_present_is_answer_only(monkeypatch):
    """Every matched candidate is already in the tank: no patch, but the
    visitor still gets focus pills instead of a dead-end 'no match' warning."""
    from plugins.portfolio_plugin.MCPTools import ask_tools

    monkeypatch.setattr(ask_tools, "_ask_enabled", lambda: True)

    candidates = [
        {"slug": "helix-ai", "name": "Helix AI", "ref": "CooLguNxDD/helix-ai", "source": "github"},
    ]

    async def _await(job_id, **_k):
        return {
            "status": "ready",
            "job_id": job_id,
            "query": "ai stuff",
            "projects": candidates,
            "error": "",
        }

    monkeypatch.setattr(
        ask_tools,
        "start_discovery",
        lambda *_a, **_k: {"status": "pending", "job_id": "j1", "query": "ai stuff"},
    )
    monkeypatch.setattr(ask_tools, "await_discovery", _await)

    out = await ask_tools.build_ask_overlay(
        ask_plan={"intent": "discover"},
        question="show me the ai project again",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        time_span=FROZEN_SPAN,
    )
    assert out["status"] == "ok"
    assert out["blocks"] == []
    assert out["highlight_slugs"] == ["helix-ai"]
    assert out.get("pending_job") is None


async def test_discover_empty_status_returns_non_empty_fish_pool(monkeypatch):
    """A discover intent that comes back disabled/error must still surface the
    not-in-tank recommendations the route stage already computed, so the
    visitor gets a fish_pool to spawn from instead of a bare dead end."""
    from plugins.portfolio_plugin.MCPTools import ask_tools

    monkeypatch.setattr(ask_tools, "_ask_enabled", lambda: True)
    monkeypatch.setattr(
        ask_tools,
        "start_discovery",
        lambda *_a, **_k: {"status": "disabled", "job_id": "", "query": "game"},
    )

    out = await ask_tools.build_ask_overlay(
        ask_plan={
            "intent": "discover",
            "recommendations": [
                {"slug": "fisoul", "name": "Fisoul", "in_tank": False},
                {"slug": "helix-ai", "name": "Helix AI", "in_tank": True},
            ],
            "pool_id": "pool-abc",
        },
        question="tell me about your game project",
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        time_span=FROZEN_SPAN,
    )
    assert out["status"] == "ok"
    assert out["blocks"] == []
    assert [p["slug"] for p in out["fish_pool"]] == ["fisoul"]
    assert out["pool_id"] == "pool-abc"


async def test_spawn_pooled_fish_patches_fishtank_block(monkeypatch):
    from plugins.portfolio_plugin.ask import fish_pool
    from plugins.portfolio_plugin.MCPTools import ask_tools

    fish_pool.reset_pools()
    monkeypatch.setattr(ask_tools, "_ask_enabled", lambda: True)

    pool_id = fish_pool.stash_pool(
        "visitor-1",
        [
            {
                "slug": "fisoul",
                "name": "Fisoul",
                "summary": "GitHub repository CooLguNxDD/Fisoul",
                "links": [{"label": "repo", "href": "https://github.com/CooLguNxDD/Fisoul"}],
            }
        ],
        tenant_id=1,
    )

    out = await ask_tools.spawn_pooled_fish(
        pool_id=pool_id,
        slugs=["fisoul"],
        block_index=INDEX,
        tank_slugs=["helix-ai"],
        time_span=FROZEN_SPAN,
    )
    assert out["status"] == "ok"
    assert [b["type"] for b in out["blocks"]] == ["fishTank"]
    slugs = {f["slug"] for f in out["blocks"][0]["props"]["fish"]}
    assert "fisoul" in slugs
    assert out.get("pending_job") is None
    assert out["pool_id"] == pool_id


async def test_spawn_pooled_fish_respects_max_spawn_cap(monkeypatch):
    from plugins.portfolio_plugin.ask import fish_pool
    from plugins.portfolio_plugin.MCPTools import ask_tools

    fish_pool.reset_pools()
    monkeypatch.setattr(ask_tools, "_ask_enabled", lambda: True)
    monkeypatch.setattr(ask_tools, "MAX_DISCOVERY_SPAWN", 2)

    projects = [
        {
            "slug": f"proj-{i}",
            "name": f"Proj {i}",
            "summary": "s",
            "links": [{"label": "repo", "href": f"https://github.com/CooLguNxDD/proj-{i}"}],
        }
        for i in range(4)
    ]
    pool_id = fish_pool.stash_pool("visitor-2", projects, tenant_id=1)

    out = await ask_tools.spawn_pooled_fish(
        pool_id=pool_id,
        slugs=[p["slug"] for p in projects],
        block_index=INDEX,
        tank_slugs=[],
        time_span=FROZEN_SPAN,
    )
    assert out["status"] == "ok"
    spawned_slugs = {f["slug"] for f in out["blocks"][0]["props"]["fish"]}
    assert len(spawned_slugs) <= 2


async def test_spawn_pooled_fish_unknown_pool_degrades_to_answer_only(monkeypatch):
    from plugins.portfolio_plugin.ask import fish_pool
    from plugins.portfolio_plugin.MCPTools import ask_tools

    fish_pool.reset_pools()
    monkeypatch.setattr(ask_tools, "_ask_enabled", lambda: True)

    out = await ask_tools.spawn_pooled_fish(
        pool_id="does-not-exist",
        slugs=["fisoul"],
        block_index=INDEX,
        tank_slugs=[],
    )
    assert out["status"] == "ok"
    assert out["blocks"] == []
    assert any("pool expired or no matching slugs" in w for w in out["warnings"])
    assert out["pool_id"] == "does-not-exist"


async def test_discover_turn_without_a_tank_is_answer_only(monkeypatch):
    from plugins.portfolio_plugin.MCPTools import ask_tools

    monkeypatch.setattr(ask_tools, "_ask_enabled", lambda: True)

    async def _await(job_id, **_k):
        return {
            "status": "ready",
            "job_id": job_id,
            "query": "game",
            "projects": CANDIDATES,
            "error": "",
        }

    monkeypatch.setattr(
        ask_tools,
        "start_discovery",
        lambda *_a, **_k: {"status": "pending", "job_id": "j1", "query": "game"},
    )
    monkeypatch.setattr(ask_tools, "await_discovery", _await)

    out = await ask_tools.build_ask_overlay(
        ask_plan={"intent": "discover"},
        question="tell me about your game project",
        block_index=[{"id": "h1", "type": "hero"}],
        tank_slugs=[],
    )
    assert out["status"] == "ok"
    assert out["blocks"] == []
    assert any("no fishTank" in w for w in out["warnings"])
    assert out.get("pending_job") is None
