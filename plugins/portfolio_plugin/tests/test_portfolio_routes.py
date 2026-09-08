"""Unit tests for Portfolio Plugin routes."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from starlette.responses import JSONResponse

from plugins.portfolio_plugin.routes import (
    portfolio_layout,
    portfolio_job_layout,
    portfolio_layout_for_query,
    portfolio_compose,
    portfolio_agent_status,
    register_routes,
    PATH,
    JOB_LAYOUT_PATH,
    LAYOUT_FOR_QUERY_PATH,
    AGENT_STATUS_PATH,
    COMPOSE_PATH,
)
from core.http_route_registry import AuthPolicy


@pytest.mark.asyncio
@patch("plugins.portfolio_plugin.compose.floor.build_floor_layout", new_callable=AsyncMock)
async def test_200_bare_layout(mock_compose):
    """Verify that a successful layout composition returns the bare layout with correct headers."""
    expected_layout = {
        "version": 1,
        "meta": {"audience": "default", "generatedAt": "x"},
        "blocks": [],
    }
    mock_compose.return_value = expected_layout

    mock_request = MagicMock()
    mock_request.query_params = {}

    response = await portfolio_layout(mock_request)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 200
    assert json.loads(response.body) == expected_layout
    assert response.headers.get("Cache-Control") == "public, max-age=60"
    mock_compose.assert_called_once()
    kwargs = mock_compose.call_args.kwargs
    assert kwargs.get("audience") == "default"
    assert kwargs.get("refresh") is False


@pytest.mark.asyncio
@patch("plugins.portfolio_plugin.compose.floor.build_floor_layout", new_callable=AsyncMock)
async def test_bad_audience_coerced(mock_compose):
    """Verify that an invalid audience is coerced to 'default'."""
    mock_compose.return_value = {}
    
    mock_request = MagicMock()
    mock_request.query_params = {"audience": "alien"}

    await portfolio_layout(mock_request)

    mock_compose.assert_called_once()
    kwargs = mock_compose.call_args.kwargs
    assert kwargs.get("audience") == "default"
    assert kwargs.get("refresh") is False


@pytest.mark.asyncio
@patch("plugins.portfolio_plugin.compose.floor.build_floor_layout", new_callable=AsyncMock)
async def test_valid_audience_passthrough(mock_compose):
    """Verify that a valid audience is passed through correctly."""
    mock_compose.return_value = {}

    mock_request = MagicMock()
    mock_request.query_params = {"audience": "recruiter"}

    await portfolio_layout(mock_request)

    mock_compose.assert_called_once()
    kwargs = mock_compose.call_args.kwargs
    assert kwargs.get("audience") == "recruiter"
    assert kwargs.get("refresh") is False


@pytest.mark.asyncio
@patch("plugins.portfolio_plugin.compose.floor.build_floor_layout", new_callable=AsyncMock)
async def test_composer_error_503(mock_compose):
    """Verify that an error in composer returns a 503 JSONResponse."""
    mock_compose.side_effect = RuntimeError("database connection failed")

    mock_request = MagicMock()
    mock_request.query_params = {}

    response = await portfolio_layout(mock_request)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert json.loads(response.body) == {"error": "layout_unavailable"}


@patch("plugins.portfolio_plugin.routes.http_route_registry", new_callable=MagicMock)
def test_register_routes_public(mock_registry):
    """Verify that routes are registered with public authentication policy."""
    from plugins.portfolio_plugin.routes import (
        DESIGN_CONTEXT_PATH,
        portfolio_design_context,
    )

    register_routes()
    assert mock_registry.register_http_route.call_count == 8
    mock_registry.register_http_route.assert_any_call(
        PATH,
        portfolio_layout,
        methods=["GET"],
        name="portfolio_layout",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    mock_registry.register_http_route.assert_any_call(
        JOB_LAYOUT_PATH,
        portfolio_job_layout,
        methods=["GET"],
        name="portfolio_job_layout",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    mock_registry.register_http_route.assert_any_call(
        LAYOUT_FOR_QUERY_PATH,
        portfolio_layout_for_query,
        methods=["GET"],
        name="portfolio_layout_for_query",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    mock_registry.register_http_route.assert_any_call(
        DESIGN_CONTEXT_PATH,
        portfolio_design_context,
        methods=["GET"],
        name="portfolio_design_context",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    mock_registry.register_http_route.assert_any_call(
        AGENT_STATUS_PATH,
        portfolio_agent_status,
        methods=["GET"],
        name="portfolio_agent_status",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )
    from plugins.portfolio_plugin.routes import ASSET_PATH, portfolio_asset

    mock_registry.register_http_route.assert_any_call(
        ASSET_PATH,
        portfolio_asset,
        methods=["GET"],
        name="portfolio_asset",
        owner="portfolio_plugin",
        auth_policy=AuthPolicy.PUBLIC,
    )


_EXECUTE = "core.route_registry.execute.execute_operation"


@pytest.mark.asyncio
async def test_agent_status_200_found():
    """Found status returns the privacy-safe activity payload, via the catalog."""
    activity = {"job_id": "job_789", "status": "submitted", "updated_at": "2026-07-16T09:00:00+00:00"}
    with patch(
        _EXECUTE,
        new=AsyncMock(return_value={"status": "ok", "activity": activity}),
    ) as mock_exec:
        mock_request = MagicMock()
        mock_request.query_params = {"job_id": "job_789"}

        response = await portfolio_agent_status(mock_request)

    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "ok", "activity": activity}
    assert response.headers.get("Cache-Control") == "no-store"
    # Dispatched by operation identity — no job_search import anywhere.
    args, kwargs = mock_exec.await_args
    assert args[0] == "job_search_plugin"
    assert args[1] == "get_agent_status"
    assert args[2]["job_id"] == "job_789"
    assert kwargs["caller_scopes"] is None


@pytest.mark.asyncio
async def test_agent_status_200_no_activity():
    """No matching application returns activity: null, not a 404 (this is a status widget, not a lookup)."""
    with patch(_EXECUTE, new=AsyncMock(return_value={"status": "ok", "activity": None})):
        mock_request = MagicMock()
        mock_request.query_params = {}

        response = await portfolio_agent_status(mock_request)

    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "ok", "activity": None}


@pytest.mark.asyncio
async def test_agent_status_503_on_error():
    """A dispatch failure still degrades to 503 rather than surfacing an error."""
    with patch(_EXECUTE, new=AsyncMock(side_effect=RuntimeError("db down"))):
        mock_request = MagicMock()
        mock_request.query_params = {}

        response = await portfolio_agent_status(mock_request)

    assert response.status_code == 503
    assert json.loads(response.body) == {"error": "status_unavailable"}


@pytest.mark.asyncio
async def test_job_layout_200_seeded():
    """Valid short_id with a persisted layout returns the bare layout with cache headers."""
    expected_layout = {"version": 1, "meta": {"audience": "recruiter"}, "blocks": []}
    with patch(
        "plugins.portfolio_plugin.store.get_job_layout_by_short_id",
        new=AsyncMock(return_value={"layout_json": expected_layout}),
    ):
        mock_request = MagicMock()
        mock_request.path_params = {"job_id": "whiskers_successor_992"}

        response = await portfolio_job_layout(mock_request)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 200
    assert json.loads(response.body) == expected_layout
    # Matches the sibling /public/layout TTL — a re-bake must be visible
    # within a minute, not masked for up to 5.
    assert response.headers.get("Cache-Control") == "public, max-age=60"


@pytest.mark.asyncio
async def test_job_layout_404_unknown():
    """Unknown short_id returns 404."""
    with patch(
        "plugins.portfolio_plugin.store.get_job_layout_by_short_id",
        new=AsyncMock(return_value=None),
    ):
        mock_request = MagicMock()
        mock_request.path_params = {"job_id": "unknown_000"}

        response = await portfolio_job_layout(mock_request)

    assert response.status_code == 404
    assert json.loads(response.body) == {"error": "not_found"}


@pytest.mark.asyncio
async def test_job_layout_400_malformed_id():
    """Malformed job_id (path traversal / invalid chars) is rejected before any DB lookup."""
    mock_request = MagicMock()
    mock_request.path_params = {"job_id": "../etc/passwd"}

    response = await portfolio_job_layout(mock_request)

    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "invalid_job_id"}


@pytest.mark.asyncio
async def test_layout_for_query_200_ok():
    """Valid query returns envelope {layout, mode, …} with no-store cache headers."""
    expected_layout = {"version": 1, "meta": {"audience": "recruiter", "mode": "scoped"}, "blocks": []}
    with patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.generate_layout_for_query",
        new=AsyncMock(
            return_value={
                "status": "ok",
                "layout": expected_layout,
                "audience": "recruiter",
                "mode": "scoped",
                "scoped_project_count": 2,
            }
        ),
    ):
        mock_request = MagicMock()
        mock_request.query_params = {"query": "show me your SRE work unique-cache-key"}

        response = await portfolio_layout_for_query(mock_request)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["layout"] == expected_layout
    assert body["mode"] == "scoped"
    assert response.headers.get("Cache-Control") == "no-store"


@pytest.mark.asyncio
async def test_layout_for_query_400_missing_query():
    """Missing/blank query is rejected before calling the tool."""
    mock_request = MagicMock()
    mock_request.query_params = {"query": "   "}

    response = await portfolio_layout_for_query(mock_request)

    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "missing_query"}


@pytest.mark.asyncio
async def test_layout_for_query_400_on_tool_error():
    """Tool-level error status is surfaced as a 400, not swallowed as 200."""
    with patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.generate_layout_for_query",
        new=AsyncMock(return_value={"status": "error", "error": "missing_required_fields"}),
    ):
        mock_request = MagicMock()
        mock_request.query_params = {"query": "hello"}

        response = await portfolio_layout_for_query(mock_request)

    assert response.status_code == 400
    assert json.loads(response.body) == {"status": "error", "error": "missing_required_fields"}


@pytest.mark.asyncio
async def test_layout_for_query_503_on_exception():
    """Unexpected exception returns 503, not a raw traceback."""
    with patch(
        "plugins.portfolio_plugin.MCPTools.portfolio_tools.generate_layout_for_query",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        mock_request = MagicMock()
        mock_request.query_params = {"query": "hello"}

        response = await portfolio_layout_for_query(mock_request)

    assert response.status_code == 503
    assert json.loads(response.body) == {"error": "layout_unavailable"}


@pytest.mark.asyncio
async def test_job_layout_503_on_error():
    """DB lookup failure returns 503, not a raw exception."""
    with patch(
        "plugins.portfolio_plugin.store.get_job_layout_by_short_id",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        mock_request = MagicMock()
        mock_request.path_params = {"job_id": "whiskers_successor_992"}

        response = await portfolio_job_layout(mock_request)

    assert response.status_code == 503
    assert json.loads(response.body) == {"error": "layout_unavailable"}


@pytest.mark.asyncio
async def test_portfolio_compose_defaults_refresh_false():
    """Omitted refresh must not trigger live enrich (matches portfolio_layout)."""
    import plugins.portfolio_plugin.routes as routes_mod

    routes_mod._compose_cache.clear()
    expected_layout = {"version": 1, "meta": {"audience": "default"}, "blocks": []}
    with patch(
        "plugins.portfolio_plugin.compose.intent_compose.compose_intent_layout",
        new=AsyncMock(
            return_value={
                "status": "ok",
                "layout": expected_layout,
                "mode": "scoped",
                "audience": "default",
                "fragments": [],
            }
        ),
    ) as mock_compose:
        mock_request = MagicMock()
        mock_request.query_params = {}
        mock_request.json = AsyncMock(return_value={"query": "compose-refresh-default-unique"})

        response = await portfolio_compose(mock_request)

    assert response.status_code == 200
    mock_compose.assert_awaited_once()
    assert mock_compose.await_args.kwargs.get("refresh") is False


@pytest.mark.asyncio
async def test_portfolio_compose_normalizes_cache_key():
    """Case/whitespace variants of query hit the same 60s cache entry."""
    import plugins.portfolio_plugin.routes as routes_mod

    routes_mod._compose_cache.clear()
    expected_layout = {"version": 1, "meta": {}, "blocks": []}
    with patch(
        "plugins.portfolio_plugin.compose.intent_compose.compose_intent_layout",
        new=AsyncMock(
            return_value={
                "status": "ok",
                "layout": expected_layout,
                "mode": "template",
                "audience": "default",
                "fragments": [],
            }
        ),
    ) as mock_compose:
        req1 = MagicMock()
        req1.query_params = {}
        req1.json = AsyncMock(return_value={"query": "Show  CacheNormUnique"})
        r1 = await portfolio_compose(req1)
        req2 = MagicMock()
        req2.query_params = {}
        req2.json = AsyncMock(return_value={"query": "show cachenormunique"})
        r2 = await portfolio_compose(req2)

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Second call served from cache — compose only invoked once.
    assert mock_compose.await_count == 1

