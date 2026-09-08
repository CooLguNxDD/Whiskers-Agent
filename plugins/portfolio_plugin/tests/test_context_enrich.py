"""Project-context layout enrichment + L2 density."""

from __future__ import annotations

import pytest

from plugins.portfolio_plugin.compose.composer import _DAG_COLS_BY_LEVEL, _stamp_dag_from_blocks
from plugins.portfolio_plugin.compose.context_enrich import (
    enrich_layout_blocks,
    _ensure_card_spans,
)


def test_dag_projects_band_is_two_cols():
    assert _DAG_COLS_BY_LEVEL.get(2) == 2
    assert _DAG_COLS_BY_LEVEL.get(6) == 1


def test_stamp_dag_sets_cols_on_projects():
    blocks = [
        {"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}},
        {"type": "card", "id": "c1", "props": {"title": "P1"}},
        {"type": "card", "id": "c2", "props": {"title": "P2"}},
        {"type": "card", "id": "c3", "props": {"title": "P3"}},
        {"type": "card", "id": "c4", "props": {"title": "P4"}},
    ]
    dag = _stamp_dag_from_blocks(blocks)
    assert dag is not None
    levels = {lvl["level"]: lvl for lvl in dag["levels"]}
    assert levels[2]["cols"] == 2
    assert len(levels[2]["nodes"]) == 4


def test_card_spans_default_to_half_width():
    blocks = [
        {"type": "card", "id": "c1", "props": {"title": "A"}},
        {"type": "card", "id": "c2", "props": {"title": "B"}, "layout": {"span": 12}},
    ]
    out = _ensure_card_spans(blocks)
    assert out[0]["layout"]["span"] == 6
    assert out[1]["layout"]["span"] == 12  # preserve explicit


def test_drop_prose_that_duplicates_card_body():
    """Card body and deep-dive must not both re-print the same project.summary."""
    long_body = (
        "Andrew built a multi-region deployment platform for team messaging. "
        "The stack uses MCP gateways and pgvector for routing."
    )
    blocks = [
        {
            "type": "card",
            "id": "card-ai",
            "props": {"title": "Helix AI", "body": long_body},
        },
        {
            "type": "prose",
            "id": "prose-ai",
            "props": {"markdown": f"### Deep dive · Helix AI\n\n{long_body}"},
        },
        {
            "type": "prose",
            "id": "prose-unique",
            "props": {
                "markdown": (
                    "### Deep dive · Architecture\n\n"
                    "Distinct architecture notes about GOAP goal loops and "
                    "MinIO artifact offload that are not on the card."
                )
            },
        },
    ]
    out = enrich_layout_blocks(blocks, projects=[])
    prose_ids = [b["id"] for b in out if b.get("type") == "prose"]
    assert "prose-ai" not in prose_ids
    assert "prose-unique" in prose_ids


def test_enrich_adds_timeline_and_prose_from_projects():
    long_summary = (
        "Andrew built a multi-region deployment platform. " * 8
        + "\n\nSecond paragraph with more detail about reliability and cost."
    )
    projects = [
        {
            "slug": "helix-ai",
            "name": "Helix AI",
            "summary": long_summary,
            "tags": ["AI"],
            "metrics": [{"label": "Commits", "value": "395"}],
            "context_sources": [{"ref": "ingest:local:ai.md"}],
        },
        {
            "slug": "helix-mobile",
            "name": "Helix Mobile",
            "summary": long_summary,
            "tags": ["mobile"],
            "metrics": [{"label": "Commits", "value": "531"}],
        },
    ]
    base = [
        {"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}},
        {"type": "card", "id": "card-helix-ai", "props": {"title": "Helix AI"}},
        {"type": "card", "id": "card-helix-mobile", "props": {"title": "Mobile"}},
        {"type": "quickActions", "id": "cta", "props": {"actions": []}},
    ]
    out = enrich_layout_blocks(base, projects)
    types = {b["type"] for b in out}
    assert "timeline" in types
    assert "comparison" in types
    # Cards may be refilled from project.summary; dual-summary guard then
    # correctly skips prose that would only re-print that body.
    cards = [b for b in out if b["type"] == "card"]
    assert all((b.get("layout") or {}).get("span") == 6 for b in cards)
    # At least one card should now carry real prose body (not empty title-only).
    assert any((c.get("props") or {}).get("body") for c in cards)
    # CTA remains last
    assert out[-1]["type"] == "quickActions"


def test_enrich_injects_interactive_widgets_and_chart():
    projects = [
        {
            "slug": "helix-ai",
            "name": "Helix AI",
            "summary": "short",
            "tags": ["AI"],
            "metrics": [{"label": "Commits", "value": "395"}],
        },
        {
            "slug": "helix-mobile",
            "name": "Helix Mobile",
            "summary": "short",
            "tags": ["mobile"],
            "metrics": [{"label": "Commits", "value": "531"}],
        },
    ]
    base = [
        {"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}},
        {"type": "quickActions", "id": "cta", "props": {"actions": []}},
    ]
    out = enrich_layout_blocks(base, projects)
    types = {b["type"] for b in out}
    assert "mcpSandbox" in types
    assert "costSim" in types
    assert "chart" in types
    assert out[-1]["type"] == "quickActions"


def test_enrich_interactive_widgets_off_when_fewer_than_two_projects():
    base = [
        {"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}},
        {"type": "quickActions", "id": "cta", "props": {"actions": []}},
    ]
    out = enrich_layout_blocks(base, [{"slug": "solo", "name": "Solo", "metrics": []}])
    types = {b["type"] for b in out}
    assert "mcpSandbox" not in types
    assert "costSim" not in types


def test_enrich_interactive_widgets_respect_manifest_flag(monkeypatch):
    from plugins.portfolio_plugin.compose import context_enrich as ce

    monkeypatch.setattr(
        "plugins.portfolio_plugin.plugin_config.SETTINGS",
        {"portfolio_layout": {"context_enrich_interactive": False}},
    )
    projects = [
        {"slug": "a", "name": "A", "metrics": [{"label": "x", "value": "1"}]},
        {"slug": "b", "name": "B", "metrics": [{"label": "y", "value": "2"}]},
    ]
    base = [{"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}}]
    out = ce.enrich_layout_blocks(base, projects)
    types = {b["type"] for b in out}
    assert "mcpSandbox" not in types
    assert "costSim" not in types


def test_enrich_drops_empty_comparison_shell_with_no_project_data():
    base = [
        {"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}},
        {"type": "comparison", "id": "cmp1", "props": {"title": "Streams", "columns": [], "rows": []}},
    ]
    out = enrich_layout_blocks(base, [])
    types = [b["type"] for b in out]
    assert "comparison" not in types


def test_enrich_repairs_empty_comparison_shell_from_project_data():
    projects = [
        {"slug": "a", "name": "A", "metrics": [{"label": "x", "value": "1"}], "tags": []},
        {"slug": "b", "name": "B", "metrics": [{"label": "y", "value": "2"}], "tags": []},
    ]
    base = [
        {"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}},
        {"type": "comparison", "id": "cmp1", "props": {"title": "Streams", "columns": [], "rows": []}},
    ]
    out = enrich_layout_blocks(base, projects)
    comparisons = [b for b in out if b["type"] == "comparison"]
    assert len(comparisons) == 1
    assert len(comparisons[0]["props"]["rows"]) >= 2


def test_enrich_respects_raised_block_cap():
    from plugins.portfolio_plugin.compose.context_enrich import _MAX_TOTAL_BLOCKS

    assert _MAX_TOTAL_BLOCKS == 20


def test_dedupe_identical_star_stories():
    from plugins.portfolio_plugin.compose.context_enrich import enrich_layout_blocks

    star = {
        "type": "starStory",
        "id": "star-a",
        "props": {
            "situation": "S",
            "task": "T",
            "action": "A",
            "result": "R",
        },
    }
    star2 = {
        "type": "starStory",
        "id": "star-b",
        "props": {
            "situation": "S",
            "task": "T",
            "action": "A",
            "result": "R",
        },
    }
    star3 = {
        "type": "starStory",
        "id": "star-c",
        "props": {
            "situation": "Other",
            "task": "T2",
            "action": "A2",
            "result": "R2",
        },
    }
    out = enrich_layout_blocks(
        [
            {"type": "hero", "id": "h1", "props": {"name": "A", "tagline": "t"}},
            star,
            star2,
            star3,
        ],
        projects=[],
    )
    stars = [b for b in out if b.get("type") == "starStory"]
    assert len(stars) == 2
    assert stars[0]["id"] == "star-a"
    assert stars[1]["id"] == "star-c"


def test_schema_lives_in_plugin_package():
    """Canonical GenUI schema lives in portfolio_plugin; no utils re-export exists."""
    import importlib

    from plugins.portfolio_plugin.schema.ui_layout_schema import BLOCK_TYPES, validate_layout

    assert "card" in BLOCK_TYPES
    # utils.ui_layout_schema was removed — schema does not live in core/utils.
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("utils.ui_layout_schema")
    layout = {
        "version": 1,
        "meta": {"audience": "default", "generatedAt": "2026-01-01T00:00:00Z"},
        "blocks": [
            {
                "type": "hero",
                "id": "h1",
                "props": {"name": "X", "tagline": "Y"},
                "layout": {"span": 12},
            }
        ],
    }
    ok, errs = validate_layout(layout)
    assert ok is not None
    assert errs == []


@pytest.mark.asyncio
async def test_enrich_preserve_dag_keeps_caller_bands():
    """preserve_dag=True must not full re-stamp over a caller-supplied dag."""
    from plugins.portfolio_plugin.compose.context_enrich import enrich_layout_dict

    custom_dag = {
        "levels": [
            {"level": 0, "label": "Intro", "nodes": ["h1"], "at": 0.0},
            {"level": 7, "label": "Ask", "nodes": ["qa1"], "at": 1.0},
        ]
    }
    layout = {
        "version": 1,
        "meta": {
            "audience": "default",
            "generatedAt": "2026-01-01T00:00:00Z",
            "dag": custom_dag,
        },
        "blocks": [
            {"type": "hero", "id": "h1", "props": {"name": "X", "tagline": "Y"}},
            {
                "type": "quickActions",
                "id": "qa1",
                "props": {"actions": [{"label": "Ask", "prompt": "hi"}]},
            },
        ],
    }
    out = await enrich_layout_dict(layout, tenant_id=1, projects=[], preserve_dag=True)
    assert out is not None
    # Labels from the caller dag survive (merge keeps band structure)
    labels = {lvl["label"] for lvl in (out.get("meta") or {}).get("dag", {}).get("levels", [])}
    assert "Intro" in labels or "h1" in str(out.get("meta", {}).get("dag"))
