"""Unit tests for build_layout_block — scoped, grounded, single-block composition."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.portfolio_plugin.compose.block_builder import build_layout_block_impl

_PROJECTS = [
    {
        "slug": "oct",
        "name": "Whiskers Agent",
        "summary": "MCP server with agent intelligence and multi-step tool orchestration",
        "tags": ["mcp", "infra"],
        "metrics": [{"label": "stars", "value": "10", "headline": True}],
        "links": [],
        "context_sources": [{"id": "disc:github:CooLguNxDD/OpenCat-Mcp-Full", "kind": "github", "ref": "CooLguNxDD/OpenCat-Mcp-Full"}],
    },
    {
        # Must pass filter_projects_for_layout / is_portfolio_worthy_project
        # (summary ≥80 chars or metrics/sources — thin "Portfolio site" is dropped).
        "slug": "catportfolio",
        "name": "CatPortfolio",
        "summary": "Portfolio site with GenUI layout blocks and agentic composition for job applications",
        "tags": ["frontend", "primary"],
        "metrics": [{"label": "blocks", "value": "12", "headline": True}],
        "links": [],
        "context_sources": [{"id": "disc:github:CooLguNxDD/CatPortfolio", "kind": "github", "ref": "CooLguNxDD/CatPortfolio"}],
    },
]


def _mock_list_projects(projects=None):
    return AsyncMock(return_value=list(projects if projects is not None else _PROJECTS))


@pytest.mark.asyncio
async def test_unsupported_block_type_rejected():
    out = await build_layout_block_impl("bogus", tenant_id=1)
    assert out["status"] == "error"
    assert "unsupported" in out["errors"][0]


@pytest.mark.asyncio
async def test_project_grid_scoped_by_slugs_returns_exactly_one():
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "projectGrid", tenant_id=1, slugs=["oct"]
        )
    assert out["status"] == "ok"
    assert out["block"]["type"] == "projectGrid"
    assert len(out["block"]["props"]["projects"]) == 1
    assert out["block"]["props"]["projects"][0]["id"] == "oct"
    # source_refs derived automatically from the project's context_sources
    assert "disc:github:CooLguNxDD/OpenCat-Mcp-Full" in out["source_refs"]


@pytest.mark.asyncio
async def test_project_grid_unscoped_returns_full_set():
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl("projectGrid", tenant_id=1, top_k=10)
    assert out["status"] == "ok"
    assert len(out["block"]["props"]["projects"]) == 2


@pytest.mark.asyncio
async def test_project_grid_query_scoped_uses_ranking():
    ranked = list(reversed(_PROJECTS))  # pretend rank_projects_by_query reordered
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()), \
         patch(
             "plugins.portfolio_plugin.compose.composer.rank_projects_by_query",
             new_callable=AsyncMock,
             return_value=ranked,
         ):
        out = await build_layout_block_impl("projectGrid", tenant_id=1, query="infra", top_k=1)
    assert out["status"] == "ok"
    assert len(out["block"]["props"]["projects"]) == 1
    assert out["block"]["props"]["projects"][0]["id"] == "catportfolio"


@pytest.mark.asyncio
async def test_stat_strip_empty_when_no_headline_metrics():
    # Explicit project with no headline metrics (shared _PROJECTS fixtures stay rich
    # enough for filter_projects_for_layout / unscoped grid tests).
    thin = {
        "slug": "thin",
        "name": "Thin Project",
        "summary": "A real enough summary for portfolio-worthy filtering but no headline KPIs at all",
        "tags": ["frontend"],
        "metrics": [],
        "links": [],
        "context_sources": [{"id": "disc:github:org/thin", "kind": "github", "ref": "org/thin"}],
    }
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects([thin])):
        out = await build_layout_block_impl("statStrip", tenant_id=1)
    assert out["status"] == "error"
    assert "statStrip" in out["errors"][0]


@pytest.mark.asyncio
async def test_hero_block_no_scoping_needed():
    out = await build_layout_block_impl("hero", tenant_id=1)
    assert out["status"] == "ok"
    assert out["block"]["type"] == "hero"
    assert out["source_refs"] == []


@pytest.mark.asyncio
async def test_star_story_from_authored_props():
    """starStory prefers plan props; portfolio_star is retired."""
    out = await build_layout_block_impl(
        "starStory",
        tenant_id=1,
        query="infra reliability",
        props={
            "situation": "too many tools",
            "task": "route them",
            "action": "pgvector + scopes",
            "result": "fast grounded answers",
            "tags": ["mcp"],
        },
    )
    assert out["status"] == "ok"
    assert out["block"]["type"] == "starStory"
    assert out["block"]["props"]["situation"] == "too many tools"


@pytest.mark.asyncio
async def test_star_story_from_context_meta_not_portfolio_star():
    """Fallback uses portfolio_plugin__context rows with STAR meta only."""
    stories = [{
        "metadata": {
            "situation": "S",
            "task": "T",
            "action": "A",
            "result": "R",
            "tags": [],
            "ref": "github:org/x",
        }
    }]
    with patch(
        "plugins.portfolio_plugin.compose.block_builder.search_context",
        new_callable=AsyncMock,
        return_value=stories,
    ):
        out = await build_layout_block_impl(
            "starStory", tenant_id=1, query="infra reliability"
        )
    assert out["status"] == "ok"
    assert out["block"]["type"] == "starStory"
    assert "github:org/x" in (out.get("source_refs") or [])


def test_apply_layout_hint_merges_key_wise():
    """Partial order-only hint must not wipe an existing span."""
    from plugins.portfolio_plugin.compose.block_builder import _apply_layout_hint

    block = {"type": "card", "id": "c1", "props": {"title": "T"}, "layout": {"span": 6}}
    out = _apply_layout_hint(block, {"order": 3})
    assert out["layout"]["span"] == 6
    assert out["layout"]["order"] == 3


@pytest.mark.asyncio
async def test_layout_span_stamped_on_project_grid():
    """GenUI spatial rhythm: layout.span/order must survive validation."""
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "projectGrid",
            tenant_id=1,
            slugs=["oct"],
            layout={"span": 6, "order": 10},
        )
    assert out["status"] == "ok"
    assert out["block"]["layout"]["span"] == 6
    assert out["block"]["layout"]["order"] == 10


@pytest.mark.asyncio
async def test_authored_prose_requires_source_refs():
    out = await build_layout_block_impl(
        "prose", tenant_id=1, props={"markdown": "hello"}
    )
    assert out["status"] == "error"
    assert "source_refs" in out["errors"][0]


@pytest.mark.asyncio
async def test_authored_prose_rejects_unresolvable_ref():
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects([])), \
         patch(
             "plugins.portfolio_plugin.compose.block_builder.search_context",
             new_callable=AsyncMock,
             return_value=[],
         ):
        out = await build_layout_block_impl(
            "prose", tenant_id=1, props={"markdown": "hello"},
            source_refs=["totally/unknown-ref"],
        )
    assert out["status"] == "error"
    assert "could not ground" in out["errors"][0]


@pytest.mark.asyncio
async def test_authored_prose_accepts_ref_from_search_hit():
    hits = [{
        "content_text": "readme text",
        "metadata": {"ref": "CooLguNxDD/OpenCat-Mcp-Full", "kind": "github", "source": "github"},
    }]
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects([])), \
         patch(
             "plugins.portfolio_plugin.compose.block_builder.search_context",
             new_callable=AsyncMock,
             return_value=hits,
         ):
        out = await build_layout_block_impl(
            "prose", tenant_id=1, query="infra",
            props={"markdown": "grounded copy"},
            source_refs=["CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out["status"] == "ok"
    assert out["block"]["type"] == "prose"
    assert out["source_refs"] == ["CooLguNxDD/OpenCat-Mcp-Full"]
    # citation is embedded for design_layout's meta.sources aggregation
    assert out["block"]["_sourceRefs"] == ["CooLguNxDD/OpenCat-Mcp-Full"]


@pytest.mark.asyncio
async def test_authored_prose_accepts_known_project_source_without_search():
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "prose", tenant_id=1,
            props={"markdown": "grounded via declared project source"},
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_authored_block_requires_props():
    out = await build_layout_block_impl(
        "prose", tenant_id=1, source_refs=["some/ref"]
    )
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_authored_comparison_rejects_empty_rows():
    """A title-only comparison table (0 rows) must not validate — see the
    bake-parity Playwright finding: an empty comparison shipped on a bake."""
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "comparison",
            tenant_id=1,
            props={"title": "Streams", "columns": [{"label": "A"}], "rows": []},
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out["status"] == "error"
    assert "rows" in out["errors"][0]


@pytest.mark.asyncio
async def test_authored_comparison_accepts_real_rows():
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "comparison",
            tenant_id=1,
            props={
                "title": "Streams",
                "columns": [{"label": "A"}, {"label": "B"}],
                "rows": [{"label": "Focus", "cells": ["x", "y"]}, {"label": "Cloud", "cells": ["1", "2"]}],
            },
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out["status"] == "ok"
    assert len(out["block"]["props"]["rows"]) == 2


@pytest.mark.asyncio
async def test_authored_chart_rejects_empty_series():
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "chart",
            tenant_id=1,
            kind="authored",
            props={"kind": "bar", "title": "Metrics", "series": []},
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out["status"] == "error"
    assert "series" in out["errors"][0]
    assert "props" in out["errors"][0]


@pytest.mark.asyncio
async def test_arch_diagram_thin_falls_back_to_derived():
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "archDiagram", tenant_id=1, props={"query": "system overview"}
        )
    assert out["status"] == "ok"
    assert out["block"]["type"] == "archDiagram"
    assert out["block"]["props"]["source"]


@pytest.mark.asyncio
async def test_arch_diagram_with_real_source_requires_citation():
    out = await build_layout_block_impl(
        "archDiagram", tenant_id=1,
        props={"title": "T", "kind": "mermaid", "source": "graph TD; A-->B"},
    )
    assert out["status"] == "error"
    assert "source_refs" in out["errors"][0]


@pytest.mark.asyncio
async def test_index_empty_degrades_to_db_only_selection():
    """When the semantic index is empty, query-scoped calls still succeed via
    rank_projects_by_query's own fail-open fallback (input order preserved).

    Must patch every import site of search_context — block_builder keeps a
    bound reference for virtual-project merge.
    """
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()), \
         patch(
             "plugins.portfolio_plugin.compose.block_builder.search_context",
             new_callable=AsyncMock,
             return_value=[],
         ), \
         patch(
             "plugins.portfolio_plugin.discovery.index.search_context",
             new_callable=AsyncMock,
             return_value=[],
         ):
        out = await build_layout_block_impl("projectGrid", tenant_id=1, query="anything", top_k=5)
    assert out["status"] == "ok"
    assert len(out["block"]["props"]["projects"]) == 2


@pytest.mark.asyncio
async def test_db_derived_non_dict_block_returns_clean_error():
    """If _build_db_derived ever returns a non-dict/non-list block with no
    errors, the builder must fail cleanly instead of blowing up downstream
    in _apply_layout_hint / _validate_and_stamp."""
    with patch(
        "plugins.portfolio_plugin.compose.block_builder._build_db_derived",
        new_callable=AsyncMock,
        return_value=(None, [], []),
    ):
        out = await build_layout_block_impl("hero", tenant_id=1)
    assert out["status"] == "error"
    assert "unexpected block shape" in out["errors"][0]


@pytest.mark.asyncio
async def test_resolve_projects_slugs_honours_top_k():
    """Explicit slugs previously bypassed top_k entirely (returned every
    matched slug regardless of top_k) -- Phase 1.5 fix."""
    three = _PROJECTS + [
        {
            "slug": "third",
            "name": "Third Project",
            "summary": "S",
            "tags": [],
            "metrics": [],
            "links": [],
            "context_sources": [],
        }
    ]
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects(three)):
        out = await build_layout_block_impl(
            "projectGrid",
            tenant_id=1,
            slugs=["oct", "catportfolio", "third"],
            query="infra reliability",
            top_k=2,
        )
    assert out["status"] == "ok"
    assert len(out["block"]["props"]["projects"]) == 2


@pytest.mark.asyncio
async def test_resolve_projects_slugs_ranked_by_query():
    """Multiple explicit slugs + a query must be ranked (not returned in
    arbitrary set-iteration order) before the top_k slice."""
    ranked = list(reversed(_PROJECTS))
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()), \
         patch(
             "plugins.portfolio_plugin.compose.composer.rank_projects_by_query",
             new_callable=AsyncMock,
             return_value=ranked,
         ) as mock_rank:
        out = await build_layout_block_impl(
            "projectGrid",
            tenant_id=1,
            slugs=["oct", "catportfolio"],
            query="infra",
            top_k=1,
        )
    mock_rank.assert_awaited_once()
    assert out["status"] == "ok"
    assert out["block"]["props"]["projects"][0]["id"] == "catportfolio"


@pytest.mark.asyncio
async def test_resolve_projects_single_slug_skips_ranking():
    """A single matched slug has nothing to rank -- rank_projects_by_query
    must not be called (avoids a pointless network/embedding round-trip)."""
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()), \
         patch(
             "plugins.portfolio_plugin.compose.composer.rank_projects_by_query",
             new_callable=AsyncMock,
         ) as mock_rank:
        out = await build_layout_block_impl(
            "projectGrid", tenant_id=1, slugs=["oct"], query="infra",
        )
    mock_rank.assert_not_called()
    assert out["status"] == "ok"
    assert len(out["block"]["props"]["projects"]) == 1


@pytest.mark.asyncio
async def test_kind_db_rejects_authored_only_type():
    """kind='db' is meaningless for a purely-authored type like prose --
    must fail fast with a clear message, not fall through silently."""
    out = await build_layout_block_impl(
        "prose", tenant_id=1, kind="db", props={"markdown": "x"}, source_refs=["r"],
    )
    assert out["status"] == "error"
    assert "kind" in out["errors"][0]


@pytest.mark.asyncio
async def test_kind_authored_forces_citation_path_for_db_derived_type():
    """kind='authored' on a normally DB-derived type (chart) must skip the
    auto-derive dispatch and require props + source_refs directly."""
    out = await build_layout_block_impl("chart", tenant_id=1, kind="authored")
    assert out["status"] == "error"
    assert "props" in out["errors"][0]

    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out2 = await build_layout_block_impl(
            "chart",
            tenant_id=1,
            kind="authored",
            props={
                "kind": "line",
                "title": "Narrative",
                "series": [{"name": "x", "points": [{"x": "a", "y": 1}]}],
            },
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out2["status"] == "ok"
    assert out2["block"]["type"] == "chart"


@pytest.mark.asyncio
async def test_kind_auto_default_unchanged_dispatch():
    """kind omitted (default 'auto') must be byte-identical to pre-Phase-1.3
    dispatch for a DB-derived type."""
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl("projectGrid", tenant_id=1, slugs=["oct"])
    assert out["status"] == "ok"
    assert out["block"]["type"] == "projectGrid"


@pytest.mark.asyncio
async def test_scene2d_grounded_from_real_projects():
    """scene2d is DB-derived like flowAnim -- nodes/edges come from real
    projects, never agent-invented (Phase 6b)."""
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl("scene2d", tenant_id=1)
    assert out["status"] == "ok"
    block = out["block"]
    assert block["type"] == "scene2d"
    assert block["props"]["renderer"] == "2d"
    assert block["props"]["preset"] == "orbit"
    labels = {n["label"] for n in block["props"]["nodes"]}
    assert "Whiskers Agent" in labels
    assert "CatPortfolio" in labels
    edge_targets = {e["to"] for e in block["props"]["edges"]}
    node_ids = {n["id"] for n in block["props"]["nodes"]}
    assert edge_targets <= node_ids  # every edge points at a real node


@pytest.mark.asyncio
async def test_scene2d_preset_and_palette_from_props():
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "scene2d", tenant_id=1, props={"preset": "pulse-grid", "palette": "cyan", "title": "Live Metrics"},
        )
    assert out["status"] == "ok"
    props = out["block"]["props"]
    assert props["preset"] == "pulse-grid"
    assert props["palette"] == "cyan"
    assert props["title"] == "Live Metrics"


@pytest.mark.asyncio
async def test_scene2d_invalid_preset_falls_back_to_orbit():
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "scene2d", tenant_id=1, props={"preset": "not-a-real-preset"},
        )
    assert out["status"] == "ok"
    assert out["block"]["props"]["preset"] == "orbit"


@pytest.mark.asyncio
async def test_scene2d_no_projects_falls_back_to_generic_skeleton():
    """Empty project inventory must still render something coherent (mirrors
    flowAnim's MCP Router / GOAP Orchestrator generic fallback), not a
    single orphan root node."""
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects([])):
        out = await build_layout_block_impl("scene2d", tenant_id=1)
    assert out["status"] == "ok"
    assert len(out["block"]["props"]["nodes"]) >= 3


@pytest.mark.asyncio
async def test_scene2d_scoped_by_slugs():
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl("scene2d", tenant_id=1, slugs=["oct"])
    assert out["status"] == "ok"
    labels = {n["label"] for n in out["block"]["props"]["nodes"]}
    assert "Whiskers Agent" in labels
    assert "CatPortfolio" not in labels


@pytest.mark.asyncio
async def test_fish_tank_grounded_and_validates():
    """fishTank is DB-derived; specimens validate via validate_layout."""
    from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout

    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl("fishTank", tenant_id=1)
    assert out["status"] == "ok"
    block = out["block"]
    assert block["type"] == "fishTank"
    assert block["props"]["renderer"] == "webgl"
    assert len(block["props"]["fish"]) >= 1
    for f in block["props"]["fish"]:
        for k in ("size", "depth", "speed", "glow"):
            assert 0.0 <= f[k] <= 1.0
    layout = {
        "version": 1,
        "meta": {"audience": "default", "generatedAt": "t"},
        "blocks": [block],
    }
    parsed, errs = validate_layout(layout)
    assert errs == [] or parsed is not None


@pytest.mark.asyncio
async def test_kind_db_arch_diagram_with_real_source_errors_instead_of_authoring():
    """kind='db' on archDiagram with a real props.source can't fall through to
    the authored path (that would defeat 'db skips authored') -- must error
    with actionable guidance instead."""
    out = await build_layout_block_impl(
        "archDiagram",
        tenant_id=1,
        kind="db",
        props={"title": "T", "kind": "mermaid", "source": "graph TD; A-->B"},
    )
    assert out["status"] == "error"
    assert "kind" in out["errors"][0]


@pytest.mark.asyncio
async def test_composite_requires_proportional_refs():
    """A composite with >400 chars of authored text-leaf content needs
    >= ceil(chars/400) source_refs -- one ref can't launder a whole authored
    page (Phase 2.2)."""
    long_text = "x" * 401
    props = {
        "layout": {"kind": "grid", "cols": 2},
        "children": [{"kind": "text", "markdown": long_text}],
    }
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "composite",
            tenant_id=1,
            kind="authored",
            props=props,
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out["status"] == "error"
    assert any("proportional citation" in e for e in out["errors"])

    two_projects = [
        _PROJECTS[0],
        {**_PROJECTS[1], "context_sources": [{"id": "disc:github:other/repo", "kind": "github", "ref": "other/repo"}]},
    ]
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects(two_projects)):
        out2 = await build_layout_block_impl(
            "composite",
            tenant_id=1,
            kind="authored",
            props=props,
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full", "disc:github:other/repo"],
        )
    assert out2["status"] == "ok"
    assert out2["block"]["type"] == "composite"


@pytest.mark.asyncio
async def test_composite_short_text_needs_only_one_ref():
    """Below the 400-char floor, the generic single-ref requirement is
    sufficient -- the proportional rule only kicks in above the floor."""
    props = {
        "layout": {"kind": "stack"},
        "children": [{"kind": "text", "markdown": "short grounded claim"}],
    }
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "composite",
            tenant_id=1,
            kind="authored",
            props=props,
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_composite_rejects_non_http_image_src():
    """image/media/link leaf src/href must be http(s):// -- these render on
    a public HR-facing page and become live third-party requests."""
    props = {
        "layout": {"kind": "grid", "cols": 2},
        "children": [
            {"kind": "metric", "label": "Uptime", "value": "99.9%"},
            {"kind": "image", "src": "/relative/path.png"},
        ],
    }
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "composite",
            tenant_id=1,
            kind="authored",
            props=props,
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out["status"] == "error"
    assert any("http(s)" in e for e in out["errors"])


@pytest.mark.asyncio
async def test_composite_accepts_valid_http_image_src():
    props = {
        "layout": {"kind": "grid", "cols": 2},
        "children": [
            {"kind": "metric", "label": "Uptime", "value": "99.9%"},
            {"kind": "image", "src": "https://example.com/a.png", "alt": "chart"},
        ],
    }
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "composite",
            tenant_id=1,
            kind="authored",
            props=props,
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_composite_url_guard_recurses_into_containers():
    """The URL guard must walk nested containers, not just the top-level
    children list."""
    props = {
        "layout": {"kind": "grid", "cols": 2},
        "children": [
            {
                "kind": "stack",
                "children": [
                    {"kind": "link", "href": "javascript:alert(1)", "label": "x"},
                ],
            },
        ],
    }
    with patch("plugins.portfolio_plugin.compose.block_builder.list_projects", _mock_list_projects()):
        out = await build_layout_block_impl(
            "composite",
            tenant_id=1,
            kind="authored",
            props=props,
            source_refs=["disc:github:CooLguNxDD/OpenCat-Mcp-Full"],
        )
    assert out["status"] == "error"
    assert any("http(s)" in e for e in out["errors"])
