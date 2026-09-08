"""Phase 4 — Layout Jury multi-dim critique unit tests."""

from __future__ import annotations

import pytest

from plugins.portfolio_plugin.layout.layout_jury import (
    _score_structure,
    critique_layout,
    score_layout_heuristic,
)


def _valid_layout(n_blocks: int = 5, types=None, mode="scoped", with_dag=True):
    types = types or ["hero", "kpiGrid", "card", "starStory", "archDiagram"]
    blocks = []
    for i in range(n_blocks):
        t = types[i % len(types)]
        props = {"title": "Whiskers Agent platform reliability multi-tenant MCP"}
        if t == "prose":
            props = {"markdown": "Whiskers Agent multi-tenant MCP reliability"}
        elif t == "kpiGrid":
            # Real content, not a title-only shell — the jury now penalizes
            # empty comparison/chart/timeline/kpiGrid shells (bake-parity fix).
            props = {"items": [{"label": "commits", "value": "395"}]}
        blocks.append({"type": t, "id": f"{t}-{i}", "props": props})
    meta = {
        "audience": "peer",
        "generatedAt": "2020-01-01T00:00:00Z",
        "mode": mode,
        "theme": "neon",
        "sources": [{"ref": "github:org/repo"}],
    }
    if with_dag:
        meta["dag"] = {
            "levels": [
                {"level": 0, "label": "Intro", "nodes": ["hero-0"]},
                {"level": 2, "label": "Projects", "nodes": ["card-2"], "cols": 2},
            ]
        }
    return {"version": 1, "meta": meta, "blocks": blocks}


def test_heuristic_scores_rich_layout():
    layout = _valid_layout()
    res = score_layout_heuristic(
        layout,
        query="redesign portfolio for platform engineer multi-tenant MCP",
        goal_class="redesign",
        theme="neon",
    )
    assert res["status"] == "ok"
    assert res["composite"] >= 5.0
    assert "brief_fit" in res["dimensions"]
    assert "evidence" in res["dimensions"]


def test_template_mode_penalized():
    layout = _valid_layout(mode="template", with_dag=False)
    res = score_layout_heuristic(layout, query="redesign", goal_class="redesign")
    assert any("template" in e for e in res["must_fix"])
    assert res["dimensions"]["schema_craft"] < 5


def test_thin_layout_structure_must_fix():
    layout = _valid_layout(n_blocks=1, types=["hero"], with_dag=False)
    res = score_layout_heuristic(layout, query="x", goal_class="redesign")
    assert any(e.startswith("structure:") for e in res["must_fix"])
    assert res["composite"] < 8


@pytest.mark.asyncio
async def test_critique_layout_pass_threshold_low():
    layout = _valid_layout()
    # Force low threshold so heuristic pass is stable without full schema
    res = await critique_layout(
        layout,
        query="platform engineer multi-tenant MCP Whiskers Agent",
        goal_class="redesign",
        theme="neon",
        threshold=1.0,
        use_llm=False,
    )
    assert res["status"] == "ok"
    assert "composite" in res
    assert res["threshold"] == 1.0


def _layout_with_dag(levels: list[dict], n_blocks: int = 6) -> dict:
    layout = _valid_layout(n_blocks=n_blocks, with_dag=False)
    layout["meta"]["dag"] = {"levels": levels}
    return layout


def test_structure_penalizes_single_band_dag():
    """>60% of banded blocks crowded into one level -> must_fix + penalty,
    not a flat type->band stamp masquerading as real composition."""
    crowded = _layout_with_dag(
        [
            {"level": 0, "label": "Intro", "nodes": ["hero-0"]},
            {
                "level": 6,
                "label": "Deep dive",
                "nodes": ["kpiGrid-1", "card-2", "starStory-3", "archDiagram-4", "hero-5"],
            },
        ]
    )
    spread = _layout_with_dag(
        [
            {"level": 0, "label": "Intro", "nodes": ["hero-0"]},
            {"level": 1, "label": "Impact", "nodes": ["kpiGrid-1"]},
            {"level": 2, "label": "Projects", "nodes": ["card-2", "starStory-3"]},
            {"level": 6, "label": "Deep dive", "nodes": ["archDiagram-4", "hero-5"]},
        ]
    )
    crowded_score, crowded_must = _score_structure(crowded, goal_class="redesign")
    spread_score, spread_must = _score_structure(spread, goal_class="redesign")

    assert any("single matrix band" in m for m in crowded_must)
    assert not any("single matrix band" in m for m in spread_must)
    assert crowded_score < spread_score


def test_structure_bonus_at_three_or_more_bands():
    # scoped_ask (not redesign/bake_for_job) keeps require_dag off, so the
    # base score has headroom below the 10.0 clamp for the +0.5 band bonus
    # to actually show up instead of both cases saturating at the ceiling.
    two_bands = _layout_with_dag(
        [
            {"level": 0, "label": "Intro", "nodes": ["hero-0", "kpiGrid-1", "card-2"]},
            {"level": 6, "label": "Deep dive", "nodes": ["starStory-3", "archDiagram-4", "hero-5"]},
        ]
    )
    three_bands = _layout_with_dag(
        [
            {"level": 0, "label": "Intro", "nodes": ["hero-0", "kpiGrid-1"]},
            {"level": 2, "label": "Projects", "nodes": ["card-2", "starStory-3"]},
            {"level": 6, "label": "Deep dive", "nodes": ["archDiagram-4", "hero-5"]},
        ]
    )
    two_score, _ = _score_structure(two_bands, goal_class="scoped_ask")
    three_score, _ = _score_structure(three_bands, goal_class="scoped_ask")
    assert three_score > two_score


def test_structure_rewards_visual_block():
    """scene2d or an svg-motif archDiagram earns a small bonus over an
    otherwise-identical layout with neither (Phase 6d)."""
    base = _layout_with_dag(
        [{"level": 0, "label": "Intro", "nodes": ["hero-0"]}, {"level": 1, "label": "Impact", "nodes": ["kpiGrid-1"]}],
        n_blocks=2,
    )
    with_scene2d = {
        **base,
        "blocks": base["blocks"] + [{"type": "scene2d", "id": "scene2d-1", "props": {"renderer": "2d", "preset": "orbit", "nodes": [], "edges": [], "motion": {}}}],
    }
    no_visual_score, _ = _score_structure(base, goal_class="scoped_ask")
    visual_score, _ = _score_structure(with_scene2d, goal_class="scoped_ask")
    assert visual_score > no_visual_score


def test_structure_svg_arch_diagram_counts_as_visual():
    base = _layout_with_dag([{"level": 0, "label": "Intro", "nodes": ["hero-0"]}], n_blocks=1)
    mermaid_arch = {**base, "blocks": base["blocks"] + [{"type": "archDiagram", "id": "a1", "props": {"kind": "mermaid", "source": "graph TD"}}]}
    svg_arch = {**base, "blocks": base["blocks"] + [{"type": "archDiagram", "id": "a1", "props": {"kind": "svg", "source": "<svg/>"}}]}
    mermaid_score, _ = _score_structure(mermaid_arch, goal_class="scoped_ask")
    svg_score, _ = _score_structure(svg_arch, goal_class="scoped_ask")
    assert svg_score > mermaid_score


def test_structure_penalizes_empty_content_shells():
    """A title-only comparison table (0 rows) must cost points and surface a
    must_fix — see the bake-parity Playwright finding this gate exists for.

    Both layouts have the same block count / type diversity / bands so only
    the empty-vs-filled comparison content differs -- an apples-to-apples
    comparison, not one confounded by the block-count-quality-gate bonus.
    """
    base = _layout_with_dag(
        [{"level": 0, "label": "Intro", "nodes": ["hero-0"]}],
        n_blocks=2,
    )
    filled_extra = {
        **base,
        "blocks": base["blocks"]
        + [
            {
                "type": "comparison",
                "id": "cmp-1",
                "props": {
                    "title": "Streams",
                    "columns": [{"label": "A"}, {"label": "B"}],
                    "rows": [{"label": "x", "cells": ["1", "2"]}, {"label": "y", "cells": ["3", "4"]}],
                },
            }
        ],
    }
    empty_extra = {
        **base,
        "blocks": base["blocks"]
        + [
            {
                "type": "comparison",
                "id": "cmp-1",
                "props": {"title": "Streams", "columns": [], "rows": []},
            }
        ],
    }
    filled_score, filled_must = _score_structure(filled_extra, goal_class="scoped_ask")
    empty_score, empty_must = _score_structure(empty_extra, goal_class="scoped_ask")
    assert any("empty content" in m for m in empty_must)
    assert not any("empty content" in m for m in filled_must)
    assert empty_score < filled_score


def test_structure_rewards_interactive_and_chart_coverage():
    base = _layout_with_dag(
        [
            {"level": 0, "label": "Intro", "nodes": ["hero-0"]},
            {"level": 1, "label": "Impact", "nodes": ["kpiGrid-1"]},
            {"level": 2, "label": "Projects", "nodes": ["card-2"]},
        ],
        n_blocks=3,
    )
    base["meta"]["scopedProjectCount"] = 3
    with_coverage = {
        **base,
        "blocks": base["blocks"]
        + [
            {"type": "mcpSandbox", "id": "mcp-1", "props": {}},
            {
                "type": "chart",
                "id": "chart-1",
                "props": {"kind": "bar", "series": [{"name": "m", "points": [{"x": "a", "y": 1.0}]}]},
            },
        ],
    }
    base_score, base_must = _score_structure(base, goal_class="bake_for_job")
    coverage_score, coverage_must = _score_structure(with_coverage, goal_class="bake_for_job")
    assert coverage_score > base_score
    assert any("no interactive widget" in m for m in base_must)
    assert not any("no interactive widget" in m for m in coverage_must)


def test_structure_no_dag_unaffected_by_band_scoring():
    """A layout with no meta.dag at all must not trip either the crowding
    penalty or the multi-band bonus -- the band check is opt-in."""
    layout = _layout_with_dag([])
    layout["meta"].pop("dag")
    score, must = _score_structure(layout, goal_class="scoped_ask")
    assert not any("single matrix band" in m for m in must)
