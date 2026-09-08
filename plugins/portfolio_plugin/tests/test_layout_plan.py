"""Phase 1 — LayoutPlan IR + materializer unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from plugins.portfolio_plugin.layout.layout_plan import (
    LayoutBand,
    LayoutPlan,
    LayoutQualityTargets,
    LayoutStep,
    block_plan_to_layout_plan,
    materialize_layout_plan,
    merge_quality_floor,
    parse_layout_plan,
    plan_to_block_plan,
)


def test_parse_layout_plan_mixed_steps():
    plan = parse_layout_plan(
        {
            "version": 1,
            "recipe_id": "matrix-redesign",
            "theme": "neon",
            "steps": [
                {"id": "h1", "kind": "db", "block_type": "hero"},
                {
                    "id": "prose-1",
                    "kind": "authored",
                    "block_type": "prose",
                    "props": {"markdown": "Grounded claim."},
                    "source_refs": ["github:org/repo"],
                },
            ],
            "quality_targets": {"min_blocks": 5, "require_citations": True},
        }
    )
    assert isinstance(plan, LayoutPlan)
    assert plan.theme == "neon"
    assert len(plan.steps) == 2
    assert plan.steps[1].source_refs == ["github:org/repo"]
    assert plan.quality_targets.min_blocks == 5


def test_parse_layout_plan_rejects_bad_shape():
    with pytest.raises((ValidationError, ValueError)):
        parse_layout_plan("not-a-dict")


def test_plan_to_block_plan_roundtrip():
    plan = LayoutPlan(
        steps=[
            LayoutStep(id="c1", block_type="card", top_k=3, query="SRE"),
            LayoutStep(
                id="p1",
                kind="authored",
                block_type="prose",
                props={"markdown": "x"},
                source_refs=["ref:1"],
                layout={"span": 12},
            ),
        ]
    )
    bp = plan_to_block_plan(plan)
    assert bp[0]["block_type"] == "card"
    assert bp[0]["block_id"] == "c1"
    assert bp[0]["query"] == "SRE"
    assert bp[1]["props"]["markdown"] == "x"
    assert bp[1]["source_refs"] == ["ref:1"]
    assert bp[1]["layout"]["span"] == 12

    lifted = block_plan_to_layout_plan(bp, recipe_id="scoped-ask", theme="cozy")
    assert lifted.recipe_id == "scoped-ask"
    assert len(lifted.steps) == 2
    assert lifted.steps[1].props["markdown"] == "x"


@pytest.mark.asyncio
async def test_materialize_layout_plan_uses_compose_scoped():
    fake_layout = {
        "version": 1,
        "meta": {"audience": "peer", "generatedAt": "2020-01-01T00:00:00Z", "mode": "scoped"},
        "blocks": [
            {"type": "hero", "id": "h1", "props": {"title": "Hi", "subtitle": "Sub"}},
        ],
    }
    plan = {
        "theme": "neon",
        "theme_overrides": {"--accent": "cyan"},
        "recipe_id": "test-recipe",
        "steps": [{"id": "h1", "block_type": "hero"}],
        "brief": "redesign for platform",
    }
    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_scoped_layout",
        new_callable=AsyncMock,
        return_value={"status": "ok", "layout": fake_layout, "audience": "peer", "mode": "scoped"},
    ) as mock_compose:
        res = await materialize_layout_plan(plan, tenant_id=1, query="platform role")
    assert res["status"] == "ok"
    assert res["materializer"] == "layout_plan"
    assert res["layout"]["meta"]["recipeId"] == "test-recipe"
    assert res["layout"]["meta"]["themeOverrides"]["--accent"] == "cyan"
    mock_compose.assert_awaited_once()
    kwargs = mock_compose.await_args.kwargs
    assert kwargs["tenant_id"] == 1
    assert kwargs["block_plan"][0]["block_type"] == "hero"


@pytest.mark.asyncio
async def test_materialize_empty_plan_errors():
    res = await materialize_layout_plan({"steps": []}, tenant_id=1)
    assert res["status"] == "error"
    assert res["error"] == "empty_plan"


@pytest.mark.asyncio
async def test_materialize_authored_grounding_surfaces_via_compose():
    """Authored steps flow into block_plan with props + source_refs."""
    plan = LayoutPlan(
        steps=[
            LayoutStep(id="h1", kind="db", block_type="hero"),
            LayoutStep(
                id="prose-angle",
                kind="authored",
                block_type="prose",
                props={"markdown": "Reliability at multi-tenant scale."},
                source_refs=["github:CooLguNxDD/OpenCat-Mcp-Full"],
            ),
        ],
        brief="fintech platform",
    )
    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_scoped_layout",
        new_callable=AsyncMock,
        return_value={
            "status": "ok",
            "layout": {"version": 1, "meta": {}, "blocks": []},
            "mode": "scoped",
        },
    ) as mock_compose:
        await materialize_layout_plan(plan, tenant_id=7)
    bp = mock_compose.await_args.kwargs["block_plan"]
    authored = [s for s in bp if s.get("block_id") == "prose-angle"][0]
    assert authored["props"]["markdown"].startswith("Reliability")
    assert authored["source_refs"] == ["github:CooLguNxDD/OpenCat-Mcp-Full"]


def test_plan_to_block_plan_carries_band():
    """LayoutStep.band must reach the compose_scoped block_plan entry so
    compose_scoped_layout can drive meta.dag directly (Phase 2.1)."""
    plan = LayoutPlan(
        steps=[
            LayoutStep(id="h1", block_type="hero"),  # no band -> omitted
            LayoutStep(
                id="c1",
                block_type="card",
                band=LayoutBand(level=2, label="Projects", cols=2),
            ),
        ]
    )
    bp = plan_to_block_plan(plan)
    assert "band" not in bp[0]
    assert bp[1]["band"] == {"level": 2, "label": "Projects", "cols": 2}


def test_layout_band_bounds():
    band = LayoutBand(level=0, label="Intro")
    assert band.cols is None
    with pytest.raises(ValidationError):
        LayoutBand(level=-1, label="x")
    with pytest.raises(ValidationError):
        LayoutBand(level=0, label="x", cols=5)


def test_plan_to_block_plan_carries_kind():
    """LayoutStep.kind must reach the compose_scoped block_plan entry -- it was
    written by plan_to_block_plan but never read downstream before compose_scoped
    was taught to forward it (Phase 1.3)."""
    plan = LayoutPlan(
        steps=[
            LayoutStep(id="h1", block_type="hero"),  # kind default "authored" -> omitted
            LayoutStep(id="c1", kind="db", block_type="chart", props={"series": []}, source_refs=["r"]),
        ]
    )
    bp = plan_to_block_plan(plan)
    assert "kind" not in bp[0]
    assert bp[1]["kind"] == "db"


def test_merge_quality_floor_takes_max_of_counts():
    merged = merge_quality_floor({"min_blocks": 5, "min_types": 3}, {"min_blocks": 3, "min_types": 2})
    assert merged["min_blocks"] == 5
    assert merged["min_types"] == 3

    # Plan may raise the bar above the recipe's floor.
    merged2 = merge_quality_floor({"min_blocks": 3, "min_types": 2}, {"min_blocks": 8, "min_types": 6})
    assert merged2["min_blocks"] == 8
    assert merged2["min_types"] == 6


def test_merge_quality_floor_ors_require_flags():
    merged = merge_quality_floor(
        {"require_citations": True, "forbid_template_mode": False},
        {"require_citations": False, "require_dag": True},
    )
    assert merged["require_citations"] is True
    assert merged["require_dag"] is True


def test_merge_quality_floor_handles_none_inputs():
    """No recipe matched, agent didn't set quality_targets -> pydantic defaults,
    not a crash. min_blocks default (3) sits below min_blocks_for('bake_for_job')
    (5) -- callers rely on the goal-class floor in layout_jury._score_structure
    to still apply on top of this."""
    merged = merge_quality_floor(None, None)
    defaults = LayoutQualityTargets()
    assert merged["min_blocks"] == defaults.min_blocks
    assert merged["min_types"] == defaults.min_types
    assert merged["forbid_template_mode"] is True


@pytest.mark.asyncio
async def test_materialize_layout_plan_forwards_non_default_audience():
    """LayoutPlan.audience must reach compose_scoped_layout's audience kwarg
    (Phase 1.6) -- previously discarded, re-derived from a keyword scorer."""
    fake_layout = {
        "version": 1,
        "meta": {"audience": "recruiter", "generatedAt": "2020-01-01T00:00:00Z", "mode": "scoped"},
        "blocks": [{"type": "hero", "id": "h1", "props": {"title": "Hi", "subtitle": "Sub"}}],
    }
    plan = {"audience": "recruiter", "steps": [{"id": "h1", "block_type": "hero"}], "brief": "x"}
    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_scoped_layout",
        new_callable=AsyncMock,
        return_value={"status": "ok", "layout": fake_layout, "audience": "recruiter", "mode": "scoped"},
    ) as mock_compose:
        await materialize_layout_plan(plan, tenant_id=1)
    assert mock_compose.await_args.kwargs["audience"] == "recruiter"


@pytest.mark.asyncio
async def test_materialize_layout_plan_default_audience_not_forwarded():
    """Untouched LayoutPlan.audience ("default") must not force-skip the
    keyword inference compose_scoped_layout otherwise runs."""
    fake_layout = {
        "version": 1,
        "meta": {"audience": "peer", "generatedAt": "2020-01-01T00:00:00Z", "mode": "scoped"},
        "blocks": [{"type": "hero", "id": "h1", "props": {"title": "Hi", "subtitle": "Sub"}}],
    }
    plan = {"steps": [{"id": "h1", "block_type": "hero"}], "brief": "x"}
    with patch(
        "plugins.portfolio_plugin.compose.compose_scoped.compose_scoped_layout",
        new_callable=AsyncMock,
        return_value={"status": "ok", "layout": fake_layout, "audience": "peer", "mode": "scoped"},
    ) as mock_compose:
        await materialize_layout_plan(plan, tenant_id=1)
    assert mock_compose.await_args.kwargs["audience"] == ""
