"""Specialist discovery/index agents + notion allowlist."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.portfolio_plugin.agents.discovery import (
    _honest_capability_report,
    run_discovery_agent,
)
from plugins.portfolio_plugin.compose.blackboard import PortfolioDraft, get_draft_store
from plugins.portfolio_plugin.discovery.normalize import (
    is_notion_allowlisted,
    normalize_notion_item,
)


def test_honest_report_notion_missing_errors():
    out = _honest_capability_report(
        scope="notion",
        resolved=[{"capability": "notion.pages.search", "plugin_id": None}],
        fetched=[],
        doc_count=0,
    )
    assert out is not None
    assert out["status"] == "error"
    assert out["error"] == "discovery_capability_missing"
    assert any(e.get("capability") == "notion.pages.search" for e in out["errors"])


def test_honest_report_all_scope_partial_when_docs():
    out = _honest_capability_report(
        scope="all",
        resolved=[
            {"capability": "github.repos.list", "plugin_id": "proxy_github-1", "local_tool": None},
        ],
        fetched=[],
        doc_count=3,
    )
    assert out is not None
    assert out["status"] == "partial"


def test_honest_report_ok_when_github_local_fallback():
    out = _honest_capability_report(
        scope="github",
        resolved=[{"capability": "github.repos.list", "plugin_id": None, "local_tool": "list_owned_repos"}],
        fetched=[],
        doc_count=0,
    )
    assert out is None


def test_notion_allowlist_empty_allows_all():
    item = {"id": "abc-123", "url": "https://www.notion.so/page"}
    assert is_notion_allowlisted(item, allow={"databases": [], "page_prefixes": []}) is True


def test_notion_allowlist_prefix_filters():
    allow = {"databases": [], "page_prefixes": ["deadbeef"]}
    ok = {"id": "deadbeef-1111-2222", "url": "https://notion.so/x"}
    bad = {"id": "cafebabe-0000", "url": "https://notion.so/y"}
    assert is_notion_allowlisted(ok, allow=allow) is True
    assert is_notion_allowlisted(bad, allow=allow) is False
    assert normalize_notion_item(bad, allow=allow) is None
    doc = normalize_notion_item(
        {**ok, "properties": {"title": {"title": [{"plain_text": "Hello"}]}}},
        allow=allow,
    )
    # title extraction may still yield something from id fallback
    assert doc is None or doc.ref.startswith("deadbeef")


def test_notion_allowlist_database_parent():
    allow = {
        "databases": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
        "page_prefixes": [],
    }
    item = {
        "id": "page-1",
        "parent": {"database_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
        "url": "https://notion.so/p",
        "properties": {"Name": {"title": [{"plain_text": "Doc"}]}},
    }
    assert is_notion_allowlisted(item, allow=allow) is True
    assert (
        is_notion_allowlisted(
            {"id": "page-2", "parent": {"database_id": "other"}, "url": "u"},
            allow=allow,
        )
        is False
    )


@pytest.mark.asyncio
async def test_run_discovery_agent_folds_draft():
    draft = get_draft_store().create(goal="refresh portfolio index")
    fake = {
        "status": "ok",
        "resolved": [
            {
                "capability": "github.repos.list",
                "plugin_id": "proxy_github",
                "local_tool": None,
            }
        ],
        "fetched": [],
        "docs": [{"source": "github", "ref": "a/b", "title": "B"}],
        "doc_count": 1,
        "hints": [],
    }
    with patch(
        "plugins.portfolio_plugin.discovery.pipeline.run_discovery",
        new_callable=AsyncMock,
        return_value=fake,
    ):
        out = await run_discovery_agent(draft=draft, scope="github", dry_run=True, use_agentic=False)
    assert out["status"] in ("ok", "partial", "error")
    assert draft.phase == "discover"
    assert draft.context_docs
    assert out.get("draft", {}).get("session_id") == draft.session_id


@pytest.mark.asyncio
async def test_run_discovery_agent_notion_missing_proxy():
    fake = {
        "status": "ok",
        "resolved": [
            {"capability": "notion.pages.search", "plugin_id": None, "operation_id": None}
        ],
        "fetched": [],
        "docs": [],
        "doc_count": 0,
        "hints": [],
    }
    with patch(
        "plugins.portfolio_plugin.discovery.pipeline.run_discovery",
        new_callable=AsyncMock,
        return_value=fake,
    ):
        out = await run_discovery_agent(scope="notion", dry_run=True, use_agentic=False)
    assert out["status"] == "error"
    assert out["error"] == "discovery_capability_missing"


@pytest.mark.asyncio
async def test_run_discovery_agent_dry_run_uses_agentic_path_no_writes():
    """dry_run gates writes, not which planner runs — agentic loop is read-only-bound."""
    from core_graph.agent_loop.runner import AgentRunResult

    agent_result = AgentRunResult(
        status="ok",
        output={
            "findings": [
                {
                    "name": "Fisoul",
                    "slug": "fisoul",
                    "summary": "Actively-developed portfolio project.",
                    "context_sources": [{"ref": "me/fisoul", "kind": "github"}],
                }
            ]
        },
        steps=2,
        tool_calls=[{"name": "list_owned_repos"}],
    )
    with (
        patch("core.llm_provider_management.llm_available", return_value=True),
        patch(
            "core_graph.agent_loop.runner.run_agent",
            new_callable=AsyncMock,
            return_value=agent_result,
        ),
        patch(
            "plugins.portfolio_plugin.store.list_projects",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "plugins.portfolio_plugin.discovery.normalize.is_discovery_allowed_ref",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "plugins.portfolio_plugin.discovery.index.index_docs",
            new_callable=AsyncMock,
        ) as mock_index,
    ):
        # apply_reconcile is exercised for real: write_back=False (dry_run) is
        # already a no-op inside apply_reconcile itself, so no mock needed —
        # this also verifies the real no-write preview shape.
        out = await run_discovery_agent(scope="github", dry_run=True, do_index=True)

    assert out.get("agentic") is True
    assert out.get("dry_run") is True
    assert out.get("write_back") is False
    mock_index.assert_not_called()
    assert out.get("index", {}).get("status") == "skipped"
    assert out.get("index", {}).get("reason") == "dry_run"
    assert out.get("reconcile", {}).get("written") is False


# ── specialist pipeline discover gating (PR #232 review thread) ───────────────


@pytest.mark.asyncio
async def test_pipeline_discover_goal_commits():
    """An explicit discover goal refreshes the inventory for real (indexes, no dry_run).

    Previously the call site hardcoded dry_run=True/do_index=False, so the one
    goal class that exists to refresh the inventory could never actually do it.
    """
    from plugins.portfolio_plugin import pipeline as pipe

    with patch.object(
        pipe,
        "run_discovery_agent",
        new_callable=AsyncMock,
        return_value={"status": "ok", "doc_count": 3},
    ) as mock_disc:
        out = await pipe.run_portfolio_pipeline("discover my repos", tenant_id=1)

    kwargs = mock_disc.await_args.kwargs
    assert kwargs["dry_run"] is False
    assert kwargs["do_index"] is True
    # write_back is pinned False, NOT left to settings.discovery.write_back
    # (shipped true): gclass is a substring match, so any goal containing
    # "discover" would otherwise upsert portfolio_projects rows.
    assert kwargs["write_back"] is False
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_pipeline_force_discover_stays_preview_only():
    """A force_discover riding on another goal must not write on the user's behalf."""
    from plugins.portfolio_plugin import pipeline as pipe

    with (
        patch.object(
            pipe,
            "run_discovery_agent",
            new_callable=AsyncMock,
            return_value={"status": "ok", "doc_count": 0},
        ) as mock_disc,
        patch.object(
            pipe,
            "run_composer_agent",
            new_callable=AsyncMock,
            return_value={"status": "error", "error": "stop_here"},
        ),
    ):
        await pipe.run_portfolio_pipeline(
            "redesign the landing page", tenant_id=1, force_discover=True
        )

    kwargs = mock_disc.await_args.kwargs
    assert kwargs["dry_run"] is True
    assert kwargs["do_index"] is False
    assert kwargs["write_back"] is False
