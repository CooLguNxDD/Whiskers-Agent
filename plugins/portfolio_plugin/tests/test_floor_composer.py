"""Floor composer — DESIGN.md-first deterministic layouts."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_build_floor_layout_never_empty_blocks():
    from plugins.portfolio_plugin.compose.floor import build_floor_layout

    projects = [
        {
            "slug": "helix-ai",
            "name": "Helix AI",
            "summary": "Agentic workflow automation with real metrics.",
            "tags": ["ai", "primary"],
            "metrics": [{"label": "latency", "value": "p95 -40%"}],
            "sort_order": 0,
            "links": [],
            "context_sources": [{"id": "x", "ref": "github:x"}],
        },
        {
            "slug": "helix-platform",
            "name": "Helix Platform",
            "summary": "Multi-tenant care platform backbone.",
            "tags": ["platform"],
            "metrics": [{"label": "tenants", "value": "12"}],
            "sort_order": 1,
            "links": [],
            "context_sources": [],
        },
    ]

    with patch(
        "plugins.portfolio_plugin.store.list_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "plugins.portfolio_plugin.compose.composer.rank_projects_by_query",
        new=AsyncMock(side_effect=lambda ps, *a, **k: ps),
    ), patch(
        "db_layer.search_content_vectors_store.search_content_vectors",
        new=AsyncMock(return_value=[]),
    ):
        layout = await build_floor_layout(
            "senior platform engineer agentic systems",
            tenant_id=1,
            audience="default",
        )

    assert isinstance(layout, dict)
    blocks = layout.get("blocks") or []
    assert len(blocks) >= 2
    types = {b.get("type") for b in blocks if isinstance(b, dict)}
    assert "hero" in types or "kpiGrid" in types or "card" in types
    # Text stays the default-safe path — floor must never emit WebGL tank.
    assert "fishTank" not in types
    # never invents fake project names
    blob = str(layout)
    assert "FakeCorp" not in blob


@pytest.mark.asyncio
async def test_build_floor_layout_covers_full_band_plan():
    """Floor emits every _BAND_PLAN type, not just the old hardcoded subset —
    the whole point of driving emission from _BAND_PLAN instead of a fixed
    hero/kpi/card/arch/star/cta sequence (bake-parity fix)."""
    from plugins.portfolio_plugin.compose.floor import FLOOR_CLONE_TYPES, build_floor_layout

    projects = [
        {
            "slug": "helix-ai",
            "name": "Helix AI",
            "summary": "Agentic workflow automation with real metrics.",
            "tags": ["ai", "primary"],
            "metrics": [{"label": "latency", "value": "p95 -40%"}],
            "sort_order": 0,
            "links": [],
            "context_sources": [{"id": "x", "ref": "github:x"}],
        },
        {
            "slug": "helix-platform",
            "name": "Helix Platform",
            "summary": "Multi-tenant care platform backbone.",
            "tags": ["platform"],
            "metrics": [{"label": "tenants", "value": "12"}],
            "sort_order": 1,
            "links": [],
            "context_sources": [],
        },
    ]

    with patch(
        "plugins.portfolio_plugin.store.list_projects",
        new=AsyncMock(return_value=projects),
    ), patch(
        "plugins.portfolio_plugin.compose.composer.rank_projects_by_query",
        new=AsyncMock(side_effect=lambda ps, *a, **k: ps),
    ), patch(
        "db_layer.search_content_vectors_store.search_content_vectors",
        new=AsyncMock(return_value=[]),
    ):
        layout = await build_floor_layout(
            "senior platform engineer agentic systems",
            tenant_id=1,
            audience="default",
        )

    types = {b.get("type") for b in (layout.get("blocks") or []) if isinstance(b, dict)}
    # widget types are zero-grounding-cost, so they must always land
    assert "mcpSandbox" in types
    assert "costSim" in types
    assert "flowAnim" in types
    assert "chart" in types
    assert set(FLOOR_CLONE_TYPES) <= {
        "hero", "kpiGrid", "card", "flowAnim", "mcpSandbox", "archDiagram",
        "chart", "costSim", "starStory", "quickActions",
    }


@pytest.mark.asyncio
async def test_floor_to_block_plan():
    from plugins.portfolio_plugin.compose.floor import floor_to_block_plan

    plan = floor_to_block_plan(
        {
            "blocks": [
                {"type": "hero", "id": "h1"},
                {"type": "kpiGrid", "id": "k1"},
            ]
        }
    )
    assert [s["block_type"] for s in plan] == ["hero", "kpiGrid"]
