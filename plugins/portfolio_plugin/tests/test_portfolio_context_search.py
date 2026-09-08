"""Unit tests for search_portfolio_context (agent-visible read over the index)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.portfolio_plugin.MCPTools.context_search_tools import search_portfolio_context


def _hit(ref, kind="readme", source="github", slug_hint=None, text="hello " * 200, sim=0.9):
    return {
        "content_text": text,
        "similarity": sim,
        "metadata": {
            "ref": ref,
            "kind": kind,
            "source": source,
            "slug_hint": slug_hint or ref,
            "title": ref,
            "url": f"https://example.com/{ref}",
            "updated_at": "2026-01-01T00:00:00Z",
            "tags": ["a"],
        },
    }


@pytest.mark.asyncio
async def test_missing_query_is_error():
    out = await search_portfolio_context(query="")
    assert out["status"] == "error"
    assert out["missing_fields"] == ["query"]


@pytest.mark.asyncio
async def test_empty_index_returns_empty_not_error():
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_search_tools.search_context",
        new_callable=AsyncMock,
        return_value=[],
    ):
        out = await search_portfolio_context(query="infra work")
    assert out["status"] == "ok"
    assert out["docs"] == []
    assert out["count"] == 0


@pytest.mark.asyncio
async def test_excerpt_truncated_and_shaped():
    long_text = "x" * 5000
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_search_tools.search_context",
        new_callable=AsyncMock,
        return_value=[_hit("a/b", text=long_text)],
    ):
        out = await search_portfolio_context(query="infra", top_k=5)
    assert out["status"] == "ok"
    doc = out["docs"][0]
    assert len(doc["excerpt"]) <= 600
    assert doc["ref"] == "a/b"
    assert "content_text" not in doc


@pytest.mark.asyncio
async def test_kind_filter():
    hits = [_hit("a/readme", kind="readme"), _hit("a/release", kind="release")]
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_search_tools.search_context",
        new_callable=AsyncMock,
        return_value=hits,
    ):
        out = await search_portfolio_context(query="x", kinds=["release"])
    assert out["count"] == 1
    assert out["docs"][0]["kind"] == "release"


@pytest.mark.asyncio
async def test_source_filter():
    hits = [_hit("a/p", source="github"), _hit("notion:p2", source="notion")]
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_search_tools.search_context",
        new_callable=AsyncMock,
        return_value=hits,
    ):
        out = await search_portfolio_context(query="x", sources=["notion"])
    assert out["count"] == 1
    assert out["docs"][0]["source"] == "notion"


@pytest.mark.asyncio
async def test_slug_filter():
    hits = [_hit("owner/oct", slug_hint="oct"), _hit("owner/other", slug_hint="other")]
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_search_tools.search_context",
        new_callable=AsyncMock,
        return_value=hits,
    ):
        out = await search_portfolio_context(query="x", slugs=["oct"])
    assert out["count"] == 1
    assert out["docs"][0]["slug_hint"] == "oct"


@pytest.mark.asyncio
async def test_top_k_respected_after_filtering():
    hits = [_hit(f"a/{i}") for i in range(20)]
    with patch(
        "plugins.portfolio_plugin.MCPTools.context_search_tools.search_context",
        new_callable=AsyncMock,
        return_value=hits,
    ):
        out = await search_portfolio_context(query="x", top_k=3)
    assert out["count"] == 3
