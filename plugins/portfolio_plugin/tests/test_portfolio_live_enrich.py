"""Unit tests for composer live enrichment (Phase 2)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from plugins.portfolio_plugin.compose.composer import (
    _live_enrich,
    _merge_live_source_into_project,
    compose_layout,
)


def test_merge_live_source_github_topics_and_metrics():
    project = {
        "slug": "oct",
        "name": "OCT",
        "summary": "old summary",
        "tags": ["MCP"],
        "metrics": [{"label": "tools", "value": "144"}],
        "context_sources": [{"kind": "github", "ref": "CooLguNxDD/OpenCat-Mcp-Full"}],
    }
    sources = [
        {
            "status": "ok",
            "kind": "github",
            "ref": "CooLguNxDD/OpenCat-Mcp-Full",
            "source_use": ["meta"],
            "meta": {
                "description": "Live description from GitHub",
                "topics": ["langgraph", "MCP"],
                "stars": 12,
                "pushed_at": "2026-07-20T12:00:00Z",
            },
            "content": None,
        }
    ]
    merged = _merge_live_source_into_project(project, sources)
    assert "langgraph" in merged["tags"]
    # MCP already present — no dupe by case
    assert sum(1 for t in merged["tags"] if str(t).lower() == "mcp") == 1
    labels = {m["label"] for m in merged["metrics"]}
    assert "stars" in labels
    assert "pushed" in labels
    assert "tools" in labels
    # summary only replaced when empty if no source_use summary
    assert merged["summary"] == "old summary"


def test_merge_live_source_summary_when_requested():
    project = {"slug": "x", "summary": "old", "tags": [], "metrics": []}
    sources = [
        {
            "status": "ok",
            "kind": "url",
            "source_use": ["summary"],
            "content": "Fresh summary line\nmore body",
            "meta": {},
        }
    ]
    merged = _merge_live_source_into_project(project, sources)
    assert merged["summary"] == "Fresh summary line"


@pytest.mark.asyncio
async def test_live_enrich_merges_ok_sources():
    projects = [
        {
            "slug": "oct",
            "name": "OCT",
            "summary": "seeded",
            "tags": [],
            "metrics": [],
            "context_sources": [{"kind": "github", "ref": "CooLguNxDD/OpenCat-Mcp-Full", "use": ["meta"]}],
        }
    ]
    ctx = {
        "status": "ok",
        "slug": "oct",
        "sources": [
            {
                "status": "ok",
                "kind": "github",
                "source_use": ["meta"],
                "meta": {"description": "live", "topics": ["goap"], "stars": 5, "pushed_at": "2026-01-01"},
            }
        ],
    }
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools.get_project_context",
        new_callable=AsyncMock,
        return_value=ctx,
    ):
        out = await _live_enrich(projects, tenant_id=1, budget_s=5.0)

    assert out[0]["tags"] == ["goap"]
    assert any(m.get("label") == "stars" for m in out[0]["metrics"])


@pytest.mark.asyncio
async def test_live_enrich_timeout_fail_open():
    """Budget timeout leaves original projects when no merges completed."""
    projects = [
        {
            "slug": "oct",
            "summary": "seeded",
            "tags": [],
            "metrics": [],
            "context_sources": [{"kind": "github", "ref": "CooLguNxDD/OpenCat-Mcp-Full"}],
        }
    ]

    async def slow_ctx(*_a, **_k):
        await asyncio.sleep(2.0)
        return {"status": "ok", "slug": "oct", "sources": []}

    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools.get_project_context",
        side_effect=slow_ctx,
    ):
        out = await _live_enrich(projects, tenant_id=1, budget_s=0.05)

    # fail-open: original row retained (or partial — no completed merge)
    assert out[0]["summary"] == "seeded"
    assert out[0]["tags"] == []


@pytest.mark.asyncio
async def test_live_enrich_skips_projects_without_sources():
    projects = [{"slug": "plain", "summary": "x", "tags": [], "metrics": []}]
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_tools.get_project_context",
        new_callable=AsyncMock,
    ) as mock_ctx:
        out = await _live_enrich(projects, tenant_id=1)
    mock_ctx.assert_not_called()
    assert out == projects


@pytest.mark.asyncio
async def test_compose_layout_refresh_calls_live_enrich():
    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=[{"slug": "oct", "name": "OCT", "summary": "s", "tags": [], "metrics": [], "context_sources": []}],
    ), patch(
        "plugins.portfolio_plugin.discovery.index.search_context",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "plugins.portfolio_plugin.compose.composer._live_enrich",
        new_callable=AsyncMock,
        side_effect=lambda projects, **kw: projects,
    ) as mock_enrich:
        await compose_layout(
            audience="default", tenant_id=1, refresh=True, _skip_scoped=True
        )
        mock_enrich.assert_awaited_once()

    with patch(
        "plugins.portfolio_plugin.compose.composer.list_projects",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "plugins.portfolio_plugin.discovery.index.search_context",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "plugins.portfolio_plugin.compose.composer._live_enrich",
        new_callable=AsyncMock,
    ) as mock_enrich:
        await compose_layout(
            audience="default", tenant_id=1, refresh=False, _skip_scoped=True
        )
        mock_enrich.assert_not_awaited()
