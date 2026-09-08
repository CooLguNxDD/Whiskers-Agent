"""Phase 4 — portfolio layout harness unit tests.

Retrieve/record past juried LayoutPlan structures. All writes are dark-
launched (write_wins/write_losses/seed_from_memory default False in
layout_config._LAYOUT_HARNESS_DEFAULTS) -- tests explicitly flip the flag
under test via a patched get_portfolio_layout_config.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.portfolio_plugin.layout.layout_harness import (
    LayoutHarnessContext,
    format_layout_harness_block,
    record_layout_antipattern,
    record_layout_success,
    retrieve_layout_harness,
    seed_plan_from_memory,
)
from plugins.portfolio_plugin.layout.layout_plan import LayoutPlan, LayoutStep


def _cfg(**overrides) -> dict:
    base = {
        "enabled": True,
        "win_top_k": 3,
        "loss_top_k": 3,
        "similarity_threshold": 0.78,
        "max_block_chars": 2000,
        "min_score_to_record": 6.0,
        "write_wins": False,
        "write_losses": False,
        "seed_from_memory": False,
    }
    base.update(overrides)
    return {"layout_harness": base}


_LAYOUT = {
    "version": 1,
    "meta": {
        "audience": "peer",
        "generatedAt": "2020-01-01T00:00:00Z",
        "dag": {"levels": [{"level": 0, "label": "Intro", "nodes": ["hero-0"]}]},
    },
    "blocks": [{"type": "hero", "id": "hero-0", "props": {}}],
}
_JURY_PASS = {"composite": 8.4, "passed": True, "must_fix": [], "dimensions": {"brief_fit": 8.0}}
_JURY_FAIL = {"composite": 5.1, "passed": False, "must_fix": ["structure: too thin"], "dimensions": {}}
_PLAN = LayoutPlan(steps=[LayoutStep(id="hero-0", block_type="hero")], audience="peer", theme="neon")


@pytest.mark.asyncio
async def test_disabled_is_noop():
    """enabled=False -> retrieve returns an empty disabled context, writes
    are no-ops, no store function is ever called."""
    with patch(
        "plugins.portfolio_plugin.layout.layout_config.get_portfolio_layout_config",
        return_value=_cfg(enabled=False),
    ), patch(
        "plugins.portfolio_plugin.layout.layout_harness._namespace_registered", return_value=True
    ), patch(
        "db_layer.search_content_vectors_store.add_search_content_vector", new_callable=AsyncMock
    ) as mock_add, patch(
        "db_layer.search_content_vectors_store.delete_search_content_vectors_by_meta",
        new_callable=AsyncMock,
    ) as mock_del:
        ctx = await retrieve_layout_harness("q", tenant_id=1, goal_class="redesign")
        assert ctx.enabled is False
        assert ctx.wins == [] and ctx.losses == []

        win = await record_layout_success(
            tenant_id=1, query="q", goal_class="redesign", plan=_PLAN,
            layout=_LAYOUT, jury=_JURY_PASS,
        )
        loss = await record_layout_antipattern(
            tenant_id=1, query="q", goal_class="redesign", plan=_PLAN, jury=_JURY_FAIL,
        )
    assert win is None
    assert loss is None
    mock_add.assert_not_called()
    mock_del.assert_not_called()


@pytest.mark.asyncio
async def test_write_wins_flag_gates_recording():
    """enabled=True but write_wins=False (the shipped dark-launch default)
    must still not write -- distinguishes the top-level enabled flag from
    the per-write flags."""
    with patch(
        "plugins.portfolio_plugin.layout.layout_config.get_portfolio_layout_config",
        return_value=_cfg(enabled=True, write_wins=False),
    ), patch(
        "plugins.portfolio_plugin.layout.layout_harness._namespace_registered", return_value=True
    ), patch(
        "db_layer.search_content_vectors_store.add_search_content_vector", new_callable=AsyncMock
    ) as mock_add:
        res = await record_layout_success(
            tenant_id=1, query="q", goal_class="redesign", plan=_PLAN,
            layout=_LAYOUT, jury=_JURY_PASS,
        )
    assert res is None
    mock_add.assert_not_called()


@pytest.mark.asyncio
async def test_record_success_content_varies_between_rounds():
    """Two jury rounds over materially the same layout must still produce
    two DISTINCT content_text strings (score/dims/timestamp all vary) --
    otherwise UNIQUE(collection, content_hash, model) silently no-ops the
    second write via deduped:True and the harness never actually learns."""
    captured: list[str] = []

    async def _fake_add(collection, content_text, meta, *, tenant_id):
        captured.append(content_text)
        return {"status": "ok", "id": len(captured)}

    with patch(
        "plugins.portfolio_plugin.layout.layout_config.get_portfolio_layout_config",
        return_value=_cfg(enabled=True, write_wins=True),
    ), patch(
        "plugins.portfolio_plugin.layout.layout_harness._namespace_registered", return_value=True
    ), patch(
        "db_layer.search_content_vectors_store.add_search_content_vector", side_effect=_fake_add
    ), patch(
        "db_layer.search_content_vectors_store.delete_search_content_vectors_by_meta",
        new_callable=AsyncMock, return_value=0,
    ):
        jury_round1 = {**_JURY_PASS, "composite": 7.9}
        jury_round2 = {**_JURY_PASS, "composite": 8.6}
        await record_layout_success(
            tenant_id=1, query="q", goal_class="redesign", plan=_PLAN,
            layout=_LAYOUT, jury=jury_round1,
        )
        await record_layout_success(
            tenant_id=1, query="q", goal_class="redesign", plan=_PLAN,
            layout=_LAYOUT, jury=jury_round2,
        )

    assert len(captured) == 2
    assert captured[0] != captured[1]
    assert "score=7.90" in captured[0]
    assert "score=8.60" in captured[1]


@pytest.mark.asyncio
async def test_meta_contains_no_props_or_prose():
    """Written meta must be structure-only: plan_steps carries id/block_type/
    kind/band/top_k/query, never props/prose/slugs (visitor-facing content
    has no business in a shared cross-session memory store)."""
    plan = LayoutPlan(
        steps=[
            LayoutStep(id="hero-0", block_type="hero"),
            LayoutStep(
                id="prose-1", kind="authored", block_type="prose",
                props={"markdown": "secret narrative content"},
                source_refs=["github:org/repo"], slugs=["oct"],
            ),
        ],
        audience="peer", theme="neon",
    )
    captured_meta: dict = {}

    async def _fake_add(collection, content_text, meta, *, tenant_id):
        captured_meta.update(meta)
        return {"status": "ok", "id": 1}

    with patch(
        "plugins.portfolio_plugin.layout.layout_config.get_portfolio_layout_config",
        return_value=_cfg(enabled=True, write_wins=True),
    ), patch(
        "plugins.portfolio_plugin.layout.layout_harness._namespace_registered", return_value=True
    ), patch(
        "db_layer.search_content_vectors_store.add_search_content_vector", side_effect=_fake_add
    ), patch(
        "db_layer.search_content_vectors_store.delete_search_content_vectors_by_meta",
        new_callable=AsyncMock, return_value=0,
    ):
        await record_layout_success(
            tenant_id=1, query="q", goal_class="redesign", plan=plan,
            layout=_LAYOUT, jury=_JURY_PASS,
        )

    dumped = str(captured_meta)
    assert "secret narrative content" not in dumped
    assert "github:org/repo" not in dumped
    assert "oct" not in captured_meta.get("plan_steps", [{}])[1].get("slugs", [])
    plan_steps = captured_meta["plan_steps"]
    assert plan_steps[1]["block_type"] == "prose"
    assert plan_steps[1]["kind"] == "authored"
    assert "props" not in plan_steps[1]
    assert "source_refs" not in plan_steps[1]
    assert "slugs" not in plan_steps[1]


@pytest.mark.asyncio
async def test_retrieve_filters_goal_class_at_sql():
    """retrieve_layout_harness must reach the SQL-level meta_equals filter
    (goal_class), not a Python-side post-fetch filter."""
    with patch(
        "plugins.portfolio_plugin.layout.layout_config.get_portfolio_layout_config",
        return_value=_cfg(enabled=True),
    ), patch(
        "plugins.portfolio_plugin.layout.layout_harness._namespace_registered", return_value=True
    ), patch(
        "db_layer.search_content_vectors_store.search_search_content_vectors_filtered",
        new_callable=AsyncMock, return_value=[],
    ) as mock_search:
        await retrieve_layout_harness("redesign for staff platform role", tenant_id=1, goal_class="bake_for_job")

    assert mock_search.await_count == 2  # wins + losses
    for call in mock_search.await_args_list:
        assert call.kwargs["meta_equals"] == {"goal_class": "bake_for_job"}
        assert call.kwargs["tenant_id"] == 1


@pytest.mark.asyncio
async def test_retrieve_picks_best_win_by_score():
    wins = [
        {"content_text": "a", "metadata": {"score": 6.0}, "similarity": 0.9},
        {"content_text": "b", "metadata": {"score": 8.9}, "similarity": 0.8},
    ]
    with patch(
        "plugins.portfolio_plugin.layout.layout_config.get_portfolio_layout_config",
        return_value=_cfg(enabled=True),
    ), patch(
        "plugins.portfolio_plugin.layout.layout_harness._namespace_registered", return_value=True
    ), patch(
        "db_layer.search_content_vectors_store.search_search_content_vectors_filtered",
        new_callable=AsyncMock, side_effect=[wins, []],
    ):
        ctx = await retrieve_layout_harness("q", tenant_id=1, goal_class="redesign")
    assert ctx.best is not None
    assert ctx.best["metadata"]["score"] == 8.9


@pytest.mark.asyncio
async def test_retrieve_unregistered_namespace_fails_open():
    with patch(
        "plugins.portfolio_plugin.layout.layout_config.get_portfolio_layout_config",
        return_value=_cfg(enabled=True),
    ), patch(
        "plugins.portfolio_plugin.layout.layout_harness._namespace_registered", return_value=False
    ):
        ctx = await retrieve_layout_harness("q", tenant_id=1, goal_class="redesign")
    assert ctx.enabled is False
    assert ctx.wins == []


@pytest.mark.asyncio
async def test_replace_stale_by_layout_key():
    """Every write must delete-before-insert on meta.layout_key -- without
    this, the varying 'At:' timestamp alone defeats content_hash dedupe and
    the collection grows unbounded across identical-structure re-runs."""
    delete_calls = []

    async def _fake_delete(collection, key, value, *, tenant_id):
        delete_calls.append((collection, key, value, tenant_id))
        return 1

    with patch(
        "plugins.portfolio_plugin.layout.layout_config.get_portfolio_layout_config",
        return_value=_cfg(enabled=True, write_wins=True),
    ), patch(
        "plugins.portfolio_plugin.layout.layout_harness._namespace_registered", return_value=True
    ), patch(
        "db_layer.search_content_vectors_store.add_search_content_vector",
        new_callable=AsyncMock, return_value={"status": "ok", "id": 1},
    ) as mock_add, patch(
        "db_layer.search_content_vectors_store.delete_search_content_vectors_by_meta",
        side_effect=_fake_delete,
    ):
        await record_layout_success(
            tenant_id=1, query="q", goal_class="redesign", plan=_PLAN,
            layout=_LAYOUT, jury=_JURY_PASS,
        )

    assert len(delete_calls) == 1
    collection, key, value, tenant_id = delete_calls[0]
    assert key == "layout_key"
    assert tenant_id == 1
    # delete must run strictly before the insert
    add_meta = mock_add.await_args.args[2]
    assert add_meta["layout_key"] == value


@pytest.mark.asyncio
async def test_seed_plan_from_memory_changes_steps():
    """Above both thresholds -> seed steps replaced by the remembered win's
    plan_steps (the no-LLM learning path)."""
    seed = {"steps": [{"id": "h1", "block_type": "hero"}], "recipe_id": "job-bake"}
    remembered_steps = [
        {"id": "hero-0", "block_type": "hero"},
        {"id": "composite-1", "block_type": "composite", "kind": "authored", "band": {"level": 6, "label": "Deep dive"}},
    ]
    ctx = LayoutHarnessContext(
        enabled=True,
        wins=[{"metadata": {"score": 9.0, "plan_steps": remembered_steps}, "similarity": 0.9}],
        best={"metadata": {"score": 9.0, "plan_steps": remembered_steps}, "similarity": 0.9},
    )
    out = seed_plan_from_memory(seed, ctx, similarity_threshold=0.78, min_score=8.0)
    assert out["steps"] == remembered_steps
    assert out["recipe_id"] == "job-bake"  # recipe id preserved
    assert "_seeded_from_memory" not in seed  # original dict untouched


def test_seed_plan_from_memory_below_score_threshold_unchanged():
    seed = {"steps": [{"id": "h1", "block_type": "hero"}]}
    ctx = LayoutHarnessContext(
        enabled=True,
        best={"metadata": {"score": 6.0, "plan_steps": [{"id": "x", "block_type": "hero"}]}, "similarity": 0.9},
    )
    out = seed_plan_from_memory(seed, ctx, similarity_threshold=0.78, min_score=8.0)
    assert out["steps"] == seed["steps"]


def test_seed_plan_from_memory_below_similarity_threshold_unchanged():
    seed = {"steps": [{"id": "h1", "block_type": "hero"}]}
    ctx = LayoutHarnessContext(
        enabled=True,
        best={"metadata": {"score": 9.0, "plan_steps": [{"id": "x", "block_type": "hero"}]}, "similarity": 0.5},
    )
    out = seed_plan_from_memory(seed, ctx, similarity_threshold=0.78, min_score=8.0)
    assert out["steps"] == seed["steps"]


def test_seed_plan_from_memory_disallowed_unchanged():
    """allow=False (round 1 guard) vetoes seeding outright, regardless of
    how good the remembered win is."""
    seed = {"steps": [{"id": "h1", "block_type": "hero"}]}
    ctx = LayoutHarnessContext(
        enabled=True,
        best={"metadata": {"score": 10.0, "plan_steps": [{"id": "x", "block_type": "hero"}]}, "similarity": 1.0},
    )
    out = seed_plan_from_memory(seed, ctx, similarity_threshold=0.78, min_score=8.0, allow=False)
    assert out["steps"] == seed["steps"]


def test_seed_plan_from_memory_disabled_context_unchanged():
    seed = {"steps": [{"id": "h1", "block_type": "hero"}]}
    ctx = LayoutHarnessContext(enabled=False)
    out = seed_plan_from_memory(seed, ctx, similarity_threshold=0.78, min_score=8.0)
    assert out is seed


def test_format_layout_harness_block_empty_context():
    assert format_layout_harness_block(LayoutHarnessContext(enabled=False)) == ""
    assert format_layout_harness_block(LayoutHarnessContext(enabled=True)) == ""


def test_format_layout_harness_block_renders_wins_and_losses():
    ctx = LayoutHarnessContext(
        enabled=True,
        wins=[{"content_text": "Layout redesign · score=8.4\nBrief: x", "metadata": {"score": 8.4}}],
        losses=[{"content_text": "Layout redesign · score=5.1\nBrief: x", "metadata": {"score": 5.1, "must_fix": ["structure: too thin"]}}],
    )
    block = format_layout_harness_block(ctx, max_chars=2000)
    assert "LAYOUT MEMORY" in block
    assert "Winners" in block and "8.4" in block
    assert "Avoid" in block and "too thin" in block


def test_format_layout_harness_block_respects_max_chars():
    ctx = LayoutHarnessContext(
        enabled=True,
        wins=[{"content_text": "x" * 5000, "metadata": {"score": 8.0}}],
    )
    block = format_layout_harness_block(ctx, max_chars=100)
    assert len(block) <= 100
