"""Unit tests for compose_scoped_layout — GenUI one-shot path (no regex routing)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.portfolio_plugin.compose.compose_scoped import compose_scoped_layout
from plugins.portfolio_plugin.compose.intent_compose import (
    default_fragment_page,
    pick_fragments_for_intent,
)


def test_pick_fragments_no_longer_regex_routes():
    """Keyword-specific routing is gone — same audience → same default page."""
    a = pick_fragments_for_intent("architecture system diagram sre platform", "default")
    b = pick_fragments_for_intent("totally unrelated bananas", "default")
    assert a == b
    assert a == default_fragment_page("default")
    peer = pick_fragments_for_intent("x", "peer")
    assert any(e.get("fragment") == "system.arch" for e in peer)


@pytest.mark.asyncio
async def test_compose_scoped_builds_sections_and_mode():
    hero = {
        "type": "hero",
        "id": "hero-0",
        "layout": {"span": 12, "order": 0},
        "props": {"name": "A", "tagline": "B"},
    }
    grid = {
        "type": "projectGrid",
        "id": "projectGrid-1",
        "layout": {"span": 12, "order": 10},
        "props": {
            "projects": [
                {
                    "id": "oct",
                    "name": "Whiskers Agent",
                    "summary": "MCP",
                    "tags": [],
                    "metrics": [],
                    "links": [],
                }
            ]
        },
        "_sourceRefs": ["disc:github:x/y"],
    }

    card = {
        "type": "card",
        "id": "card-oct",
        "props": {
            "title": "Whiskers Agent",
            "body": "MCP",
            "domain": "ai",
            "tags": ["MCP"],
        },
        "_sourceRefs": ["disc:github:x/y"],
    }

    async def fake_build(block_type, **kwargs):
        if block_type == "hero":
            return {"status": "ok", "block": hero, "source_refs": []}
        if block_type == "card":
            clean = {k: v for k, v in card.items() if k != "_sourceRefs"}
            return {
                "status": "ok",
                "block": clean,
                "blocks": [clean],
                "source_refs": ["disc:github:x/y"],
            }
        if block_type == "projectGrid":
            return {"status": "ok", "block": grid, "source_refs": ["disc:github:x/y"]}
        return {"status": "error", "errors": [f"skip {block_type}"]}

    ok_layout = {
        "version": 1,
        "meta": {
            "audience": "peer",
            "generatedAt": "2026-01-01T00:00:00Z",
            "mode": "scoped",
            "sources": [{"ref": "disc:github:x/y"}],
            "scopedProjectCount": 1,
        },
        "blocks": [
            hero,
            {k: v for k, v in card.items() if k != "_sourceRefs"},
        ],
    }

    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.build_layout_block_impl",
        side_effect=fake_build,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_custom_layout",
        new_callable=AsyncMock,
        return_value=(ok_layout, []),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.infer_audience_from_job_signals",
        return_value=("peer", "infra reliability"),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.get_audience_template",
        return_value=("peer", {"star_query": "infra", "max_projects": 6}),
    ):
        res = await compose_scoped_layout("show me SRE work", tenant_id=1, theme="neon")

    assert res["status"] == "ok"
    assert res["mode"] == "scoped"
    assert res["layout"]["meta"]["mode"] == "scoped"
    assert res["scoped_project_count"] == 1


@pytest.mark.asyncio
async def test_compose_scoped_card_multi_expansion_replaces_only_placeholder():
    """A 'card' step returning multiple tiles replaces exactly its own
    placeholder in `sections`, never a sibling section appended before it."""
    hero = {
        "type": "hero",
        "id": "hero-0",
        "props": {"name": "A", "tagline": "B"},
    }
    card_a = {"type": "card", "id": "card-a", "props": {"title": "A", "domain": "ai"}}
    card_b = {"type": "card", "id": "card-b", "props": {"title": "B", "domain": "ai"}}

    async def fake_build(block_type, **kwargs):
        if block_type == "hero":
            return {"status": "ok", "block": hero, "source_refs": []}
        if block_type == "card":
            # Builder collapses the multi-card fan-out into a single step
            # whose "block" is just the first tile but "blocks" carries all.
            return {
                "status": "ok",
                "block": card_a,
                "blocks": [card_a, card_b],
                "source_refs": ["disc:github:x/y"],
            }
        return {"status": "error", "errors": [f"skip {block_type}"]}

    ok_layout = {
        "version": 1,
        "meta": {
            "audience": "peer",
            "generatedAt": "2026-01-01T00:00:00Z",
            "mode": "scoped",
            "sources": [{"ref": "disc:github:x/y"}],
            "scopedProjectCount": 2,
        },
        "blocks": [hero, card_a, card_b],
    }

    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.build_layout_block_impl",
        side_effect=fake_build,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_custom_layout",
        new_callable=AsyncMock,
        return_value=(ok_layout, []),
    ) as mock_compose, patch(
        "plugins.portfolio_plugin.compose.compose_scoped.infer_audience_from_job_signals",
        return_value=("peer", "infra reliability"),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.get_audience_template",
        return_value=("peer", {"star_query": "infra", "max_projects": 6}),
    ):
        res = await compose_scoped_layout("show me projects", tenant_id=1, theme="neon")

    assert res["status"] == "ok"
    # hero placeholder still present exactly once; card placeholder replaced
    # by both expanded tiles (not duplicated, not lost).
    spec_passed = mock_compose.call_args.args[0]
    sections_passed = spec_passed["sections"]
    types_and_ids = [(s["type"], s["id"]) for s in sections_passed]
    assert types_and_ids.count(("hero", "hero-0")) == 1
    assert ("card", "card-a") in types_and_ids
    assert ("card", "card-b") in types_and_ids
    assert res["scoped_project_count"] == 2


@pytest.mark.asyncio
async def test_compose_scoped_falls_back_to_template_when_no_blocks():
    template_layout = {
        "version": 1,
        "meta": {"audience": "default", "generatedAt": "2026-01-01T00:00:00Z"},
        "blocks": [{"type": "hero", "id": "h", "props": {"name": "N", "tagline": "T"}}],
    }

    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.build_layout_block_impl",
        new_callable=AsyncMock,
        return_value={"status": "error", "errors": ["nope"]},
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_layout",
        new_callable=AsyncMock,
        return_value=template_layout,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.infer_audience_from_job_signals",
        return_value=("default", None),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.get_audience_template",
        return_value=("default", {"star_query": "x", "max_projects": 6}),
    ):
        res = await compose_scoped_layout("anything", tenant_id=1)

    assert res["status"] == "ok"
    assert res["mode"] == "template"
    # Every step in _DEFAULT_PLAN failed to build — those failures must not
    # be silently swallowed (bake-parity fix: agent needs to see what dropped).
    assert res["step_errors"]
    assert all("nope" in e for e in res["step_errors"])


@pytest.mark.asyncio
async def test_compose_scoped_returns_step_errors_for_partial_failure():
    """One step fails to build, the rest succeed — step_errors reports the
    failure even though the overall compose still ships a real layout."""
    from plugins.portfolio_plugin.compose.compose_scoped import compose_scoped_layout

    ok_block = {"type": "hero", "id": "h1", "props": {"name": "N", "tagline": "T"}}

    async def _fake_build(btype, **_kwargs):
        if btype == "chart":
            return {"status": "error", "errors": ["chart: no metrics"]}
        return {"status": "ok", "block": {**ok_block, "type": btype, "id": f"{btype}-1"}, "source_refs": []}

    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.build_layout_block_impl",
        new_callable=AsyncMock,
        side_effect=_fake_build,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_custom_layout",
        new_callable=AsyncMock,
        return_value=(
            {
                "version": 1,
                "meta": {"audience": "default", "generatedAt": "2026-01-01T00:00:00Z"},
                "blocks": [ok_block],
            },
            [],
        ),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.infer_audience_from_job_signals",
        return_value=("default", None),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.get_audience_template",
        return_value=("default", {"star_query": "x", "max_projects": 6}),
    ):
        res = await compose_scoped_layout(
            "anything",
            tenant_id=1,
            block_plan=[
                {"block_type": "hero", "block_id": "h1"},
                {"block_type": "chart", "block_id": "c1"},
            ],
        )

    assert res["status"] == "ok"
    assert res["mode"] == "scoped"
    assert any("chart" in e for e in res.get("step_errors") or [])


@pytest.mark.asyncio
async def test_plan_audience_overrides_inferred():
    """audience kwarg override (Phase 1.6) skips infer_audience_from_job_signals
    entirely when the id is recognized -- this is how LayoutPlan.audience, which
    materialize_layout_plan previously computed and then discarded, actually
    reaches the composed layout."""
    hero = {"type": "hero", "id": "hero-0", "props": {"name": "A", "tagline": "B"}}

    async def fake_build(block_type, **kwargs):
        return {"status": "ok", "block": hero, "source_refs": []}

    ok_layout = {
        "version": 1,
        "meta": {"audience": "recruiter", "generatedAt": "2026-01-01T00:00:00Z", "mode": "scoped"},
        "blocks": [hero],
    }

    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.build_layout_block_impl",
        side_effect=fake_build,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_custom_layout",
        new_callable=AsyncMock,
        return_value=(ok_layout, []),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.infer_audience_from_job_signals",
    ) as mock_infer, patch(
        "plugins.portfolio_plugin.compose.compose_scoped.get_audience_template",
        side_effect=lambda a: (a, {"star_query": f"{a} query", "max_projects": 6}),
    ):
        res = await compose_scoped_layout(
            "show me work",
            tenant_id=1,
            block_plan=[{"block_type": "hero"}],
            audience="recruiter",
        )

    mock_infer.assert_not_called()
    assert res["audience"] == "recruiter"


@pytest.mark.asyncio
async def test_unrecognized_audience_falls_back_to_inference():
    """An audience override that get_audience_template can't recognize (it
    normalizes to "default") must fall through to the keyword scorer, not
    silently force "default"."""
    hero = {"type": "hero", "id": "hero-0", "props": {"name": "A", "tagline": "B"}}

    async def fake_build(block_type, **kwargs):
        return {"status": "ok", "block": hero, "source_refs": []}

    ok_layout = {
        "version": 1,
        "meta": {"audience": "peer", "generatedAt": "2026-01-01T00:00:00Z", "mode": "scoped"},
        "blocks": [hero],
    }

    def fake_get_audience_template(a):
        return (a, {"star_query": "x", "max_projects": 6}) if a in ("peer", "default") else ("default", {"star_query": "x"})

    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.build_layout_block_impl",
        side_effect=fake_build,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_custom_layout",
        new_callable=AsyncMock,
        return_value=(ok_layout, []),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.infer_audience_from_job_signals",
        return_value=("peer", "infra"),
    ) as mock_infer, patch(
        "plugins.portfolio_plugin.compose.compose_scoped.get_audience_template",
        side_effect=fake_get_audience_template,
    ):
        res = await compose_scoped_layout(
            "show me work",
            tenant_id=1,
            block_plan=[{"block_type": "hero"}],
            audience="not-a-real-audience",
        )

    mock_infer.assert_called_once()
    assert res["audience"] == "peer"


@pytest.mark.asyncio
async def test_step_kind_forwarded_to_build_layout_block_impl():
    """LayoutStep.kind ("authored"/"db") must reach build_layout_block_impl's
    kind kwarg -- previously written by plan_to_block_plan but never read."""
    hero = {"type": "hero", "id": "hero-0", "props": {"name": "A", "tagline": "B"}}
    seen_kinds: list[str] = []

    async def fake_build(block_type, **kwargs):
        seen_kinds.append(kwargs.get("kind"))
        return {"status": "ok", "block": hero, "source_refs": []}

    ok_layout = {
        "version": 1,
        "meta": {"audience": "default", "generatedAt": "2026-01-01T00:00:00Z", "mode": "scoped"},
        "blocks": [hero],
    }

    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.build_layout_block_impl",
        side_effect=fake_build,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_custom_layout",
        new_callable=AsyncMock,
        return_value=(ok_layout, []),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.infer_audience_from_job_signals",
        return_value=("default", "x"),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.get_audience_template",
        return_value=("default", {"star_query": "x", "max_projects": 6}),
    ):
        await compose_scoped_layout(
            "q",
            tenant_id=1,
            block_plan=[
                {"block_type": "hero", "kind": "authored"},
                {"block_type": "hero", "block_id": "h2"},
            ],
        )

    assert seen_kinds == ["authored", "auto"]


@pytest.mark.asyncio
async def test_plan_bands_become_meta_dag():
    """LayoutStep.band drives meta.dag directly -- including the card
    multi-expansion, whose builder-generated ids the plan can't name in
    advance (Phase 2.1)."""
    hero = {"type": "hero", "id": "hero-0", "props": {"name": "A", "tagline": "B"}}
    card_a = {"type": "card", "id": "card-a", "props": {"title": "A", "domain": "ai"}}
    card_b = {"type": "card", "id": "card-b", "props": {"title": "B", "domain": "ai"}}
    star = {"type": "starStory", "id": "star-0", "props": {"situation": "S", "task": "T", "action": "A", "result": "R"}}

    async def fake_build(block_type, **kwargs):
        if block_type == "hero":
            return {"status": "ok", "block": hero, "source_refs": []}
        if block_type == "card":
            return {"status": "ok", "block": card_a, "blocks": [card_a, card_b], "source_refs": []}
        if block_type == "starStory":
            return {"status": "ok", "block": star, "source_refs": []}
        return {"status": "error", "errors": [f"skip {block_type}"]}

    ok_layout = {
        "version": 1,
        "meta": {"audience": "peer", "generatedAt": "2026-01-01T00:00:00Z", "mode": "scoped"},
        "blocks": [hero, card_a, card_b, star],
    }

    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.build_layout_block_impl",
        side_effect=fake_build,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_custom_layout",
        new_callable=AsyncMock,
        return_value=(ok_layout, []),
    ) as mock_compose, patch(
        "plugins.portfolio_plugin.compose.compose_scoped.infer_audience_from_job_signals",
        return_value=("peer", "infra"),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.get_audience_template",
        return_value=("peer", {"star_query": "x", "max_projects": 6}),
    ):
        await compose_scoped_layout(
            "q",
            tenant_id=1,
            block_plan=[
                {"block_type": "hero", "block_id": "hero-0", "band": {"level": 0, "label": "Intro"}},
                {"block_type": "card", "band": {"level": 2, "label": "Projects", "cols": 2}},
                {"block_type": "starStory", "band": {"level": 6, "label": "Deep dive", "cols": 1}},
            ],
        )

    spec_passed = mock_compose.call_args.args[0]
    dag = spec_passed["dag"]
    levels_by_level = {lvl["level"]: lvl for lvl in dag["levels"]}
    assert levels_by_level[0]["nodes"] == ["hero-0"]
    assert set(levels_by_level[2]["nodes"]) == {"card-a", "card-b"}
    assert levels_by_level[2]["cols"] == 2
    assert levels_by_level[6]["nodes"] == ["star-0"]
    assert levels_by_level[6]["cols"] == 1
    # levels sorted ascending, "at" progression stamped
    assert [lvl["level"] for lvl in dag["levels"]] == [0, 2, 6]
    assert dag["levels"][0]["at"] == 0.0
    assert dag["levels"][-1]["at"] == 1.0


@pytest.mark.asyncio
async def test_no_bands_falls_back_to_type_map():
    """Zero steps declaring a band -> no dag key in the assembled spec at
    all, so compose_custom_layout's own _stamp_dag_from_blocks fallback
    (type->band map) runs exactly as before Phase 2.1."""
    hero = {"type": "hero", "id": "hero-0", "props": {"name": "A", "tagline": "B"}}

    async def fake_build(block_type, **kwargs):
        return {"status": "ok", "block": hero, "source_refs": []}

    ok_layout = {
        "version": 1,
        "meta": {"audience": "peer", "generatedAt": "2026-01-01T00:00:00Z", "mode": "scoped"},
        "blocks": [hero],
    }

    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.build_layout_block_impl",
        side_effect=fake_build,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_custom_layout",
        new_callable=AsyncMock,
        return_value=(ok_layout, []),
    ) as mock_compose, patch(
        "plugins.portfolio_plugin.compose.compose_scoped.infer_audience_from_job_signals",
        return_value=("peer", "infra"),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.get_audience_template",
        return_value=("peer", {"star_query": "x", "max_projects": 6}),
    ):
        await compose_scoped_layout("q", tenant_id=1, block_plan=[{"block_type": "hero"}])

    spec_passed = mock_compose.call_args.args[0]
    assert "dag" not in spec_passed


@pytest.mark.asyncio
async def test_duplicate_band_node_ids_deduped():
    """Two steps that (mistakenly) both produce a block with the same id and
    declare different bands -- the first band wins, never both (an id
    counted in two bands would silently vanish from one via the FE's
    used-id tracking)."""
    hero = {"type": "hero", "id": "dup-id", "props": {"name": "A", "tagline": "B"}}
    also_dup = {"type": "card", "id": "dup-id", "props": {"title": "A", "domain": "ai"}}

    async def fake_build(block_type, **kwargs):
        if block_type == "hero":
            return {"status": "ok", "block": hero, "source_refs": []}
        return {"status": "ok", "block": also_dup, "source_refs": []}

    ok_layout = {
        "version": 1,
        "meta": {"audience": "peer", "generatedAt": "2026-01-01T00:00:00Z", "mode": "scoped"},
        "blocks": [hero, also_dup],
    }

    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.build_layout_block_impl",
        side_effect=fake_build,
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_custom_layout",
        new_callable=AsyncMock,
        return_value=(ok_layout, []),
    ) as mock_compose, patch(
        "plugins.portfolio_plugin.compose.compose_scoped.infer_audience_from_job_signals",
        return_value=("peer", "infra"),
    ), patch(
        "plugins.portfolio_plugin.compose.compose_scoped.get_audience_template",
        return_value=("peer", {"star_query": "x", "max_projects": 6}),
    ):
        await compose_scoped_layout(
            "q",
            tenant_id=1,
            block_plan=[
                {"block_type": "hero", "block_id": "dup-id", "band": {"level": 0, "label": "Intro"}},
                {"block_type": "card", "block_id": "dup-id", "band": {"level": 2, "label": "Projects"}},
            ],
        )

    spec_passed = mock_compose.call_args.args[0]
    dag = spec_passed["dag"]
    all_nodes = [n for lvl in dag["levels"] for n in lvl["nodes"]]
    assert all_nodes.count("dup-id") == 1
    levels_by_level = {lvl["level"]: lvl for lvl in dag["levels"]}
    assert levels_by_level[0]["nodes"] == ["dup-id"]
    assert 2 not in levels_by_level or levels_by_level[2]["nodes"] == []
