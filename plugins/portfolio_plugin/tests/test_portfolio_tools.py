"""
Unit tests for Portfolio Plugin tools.
"""

import pytest
from unittest.mock import patch, AsyncMock

from plugins.portfolio_plugin.MCPTools.portfolio_tools import (
    get_projects,
    get_star_stories,
    upsert_project,
    add_star_story,
    emit_layout,
    generate_layout_for_query,
)


@pytest.mark.asyncio
async def test_get_projects_happy_path():
    """Verify get_projects returns the expected status, count, and project lists."""
    mock_projects = [{"slug": "proj-1", "name": "Project 1"}]
    with patch("plugins.portfolio_plugin.MCPTools.portfolio_tools.list_projects", new_callable=AsyncMock) as mock_list, \
         patch("plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id") as mock_tid:
        mock_tid.get.return_value = 1
        mock_list.return_value = mock_projects
        res = await get_projects(audience="recruiter", include_inactive=True)
        assert res == {
            "status": "ok",
            "count": 1,
            "projects": mock_projects,
        }
        mock_list.assert_called_once_with(audience="recruiter", include_inactive=True, tenant_id=1)


@pytest.mark.asyncio
async def test_get_star_stories_blank_query():
    """Verify get_star_stories returns missing required fields for blank queries."""
    res = await get_star_stories(query="  ")
    assert res == {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["query"],
    }


@pytest.mark.asyncio
async def test_get_star_stories_mapping_and_malformed():
    """Deprecated get_star_stories searches context for STAR-shaped meta only."""
    mock_rows = [
        {
            "id": 1,
            "metadata": {
                "situation": "Sit 1",
                "task": "Task 1",
                "action": "Act 1",
                "result": "Res 1",
                "tags": ["tag1"],
                "ref": "github:org/repo",
            },
            "similarity": 0.95,
        },
        {
            "id": 2,
            "metadata": {
                "situation": "Sit 2",
                "task": "Task 2",
                "action": "Act 2",
            },
            "similarity": 0.8,
        },
    ]
    with patch(
        "plugins.portfolio_plugin.discovery.index.search_context",
        new_callable=AsyncMock,
        return_value=mock_rows,
    ), patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id"
    ) as mock_tid:
        mock_tid.get.return_value = 1
        res = await get_star_stories(query="test-query", k=5)
        assert res["status"] == "deprecated"
        assert res["deprecated"] is True
        assert res["count"] == 1
        assert len(res["stories"]) == 1
        assert res["stories"][0]["situation"] == "Sit 1"
        assert res["stories"][0]["ref"] == "github:org/repo"


@pytest.mark.asyncio
async def test_upsert_project_blank_slug():
    """Verify upsert_project rejects a blank slug."""
    res = await upsert_project(slug=" ")
    assert res == {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["slug"],
    }


@pytest.mark.asyncio
async def test_upsert_project_create_missing_required():
    """Verify upsert_project on a new path requires name and summary."""
    with patch("plugins.portfolio_plugin.MCPTools.portfolio_tools.get_project", new_callable=AsyncMock) as mock_get, \
         patch("plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id") as mock_tid:
        mock_tid.get.return_value = 1
        mock_get.return_value = None
        res = await upsert_project(slug="new-proj", name="", summary="  ")
        assert res == {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["name", "summary"],
        }


@pytest.mark.asyncio
async def test_upsert_project_invalid_link_href():
    """Verify upsert_project detects invalid link hrefs and returns error."""
    invalid_links = [{"label": "Invalid", "href": "ftp://ftp.test.com"}]
    with patch("plugins.portfolio_plugin.MCPTools.portfolio_tools.get_project", new_callable=AsyncMock) as mock_get, \
         patch("plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id") as mock_tid:
        mock_tid.get.return_value = 1
        mock_get.return_value = {"slug": "existing"}
        res = await upsert_project(slug="existing", links=invalid_links)
        assert res["status"] == "error"
        assert res["error"] == "invalid_fields"
        assert any("href must start with http:// or https://" in d for d in res["details"])


@pytest.mark.asyncio
async def test_upsert_project_invalid_audiences():
    """Verify upsert_project rejects audiences that contain non-string elements."""
    with patch("plugins.portfolio_plugin.MCPTools.portfolio_tools.get_project", new_callable=AsyncMock) as mock_get, \
         patch("plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id") as mock_tid:
        mock_tid.get.return_value = 1
        mock_get.return_value = {"slug": "existing"}
        res = await upsert_project(slug="existing", audiences=["recruiter", 42])
        assert res["status"] == "error"
        assert res["error"] == "invalid_fields"
        assert any("audiences[1] must be a string" in d for d in res["details"])


@pytest.mark.asyncio
async def test_add_star_story_missing_fields():
    """Verify add_star_story lists all missing required fields."""
    res = await add_star_story(situation="S", task="", action="A", result="  ")
    assert res == {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["task", "result"],
    }


@pytest.mark.asyncio
async def test_add_star_story_success():
    """add_star_story is deprecated — no write to portfolio_star."""
    res = await add_star_story(
        situation="Sit",
        task="Task",
        action="Act",
        result="Res",
        tags=["tag1"],
    )
    assert res["status"] == "deprecated"
    assert res["deprecated"] is True
    assert res.get("written") is False
    assert res.get("error") == "portfolio_star_retired"


@pytest.mark.asyncio
async def test_emit_layout():
    """Verify emit_layout passes arguments correctly and returns wrapped layout."""
    mock_layout = {"version": 1, "meta": {"audience": "peer"}, "blocks": []}
    with patch("plugins.portfolio_plugin.MCPTools.portfolio_tools.composer.compose_layout", new_callable=AsyncMock) as mock_compose, \
         patch("plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id") as mock_tid:
        mock_tid.get.return_value = 1
        mock_compose.return_value = mock_layout
        res = await emit_layout(audience="peer", star_query="test", star_k=5)
        assert res == {
            "status": "ok",
            "layout": mock_layout,
        }
        mock_compose.assert_called_once_with(
            audience="peer",
            star_query="test",
            star_k=5,
            tenant_id=1,
            refresh=True,
        )


@pytest.mark.asyncio
async def test_generate_layout_for_query_missing_query():
    """Verify generate_layout_for_query validates a required user_query."""
    res = await generate_layout_for_query(user_query="")
    assert res == {
        "status": "error",
        "error": "missing_required_fields",
        "missing_fields": ["user_query"],
    }


@pytest.mark.asyncio
async def test_generate_layout_for_query_infers_audience_and_composes():
    """Verify generate_layout_for_query uses scoped GenUI path (not regex fragments)."""
    mock_layout = {"version": 1, "meta": {"audience": "recruiter", "mode": "scoped"}, "blocks": []}
    with patch(
        "plugins.portfolio_plugin.compose.intent_compose.compose_intent_layout",
        new_callable=AsyncMock,
        return_value={
            "status": "ok",
            "layout": mock_layout,
            "audience": "recruiter",
            "star_query": "measurable impact results delivery",
            "mode": "scoped",
            "scoped_project_count": 2,
        },
    ) as mock_intent, patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.current_tenant_id"
    ) as mock_tid:
        mock_tid.get.return_value = 1

        res = await generate_layout_for_query(user_query="show me your SRE work", session_id="s-1")

        assert res["status"] == "ok"
        assert res["layout"] == mock_layout
        assert res["mode"] == "scoped"
        mock_intent.assert_awaited_once()
        kwargs = mock_intent.await_args.kwargs
        assert kwargs.get("refresh") is True
        assert kwargs.get("use_fragments") is False
