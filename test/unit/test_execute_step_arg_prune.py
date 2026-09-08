"""Fast-path arg prune: drop unknown kwargs (e.g. seeded projectId) after merge."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core_graph.node.execute_step import _execute_single


@pytest.mark.asyncio
async def test_fast_path_prunes_seeded_project_id_after_step_args_merge():
    """GOAP/context often seeds projectId; portfolio get_projects must not receive it.

    Regression: prune ran before step-args merge, so projectId was re-injected and
    raised TypeError: unexpected keyword argument 'projectId'.
    """
    received = {}

    async def get_projects(audience: str = "", include_inactive: bool = False):
        received["kwargs"] = {
            "audience": audience,
            "include_inactive": include_inactive,
        }
        # Fail loudly if unexpected kwargs slip through (mirrors real TypeError).
        return {"status": "ok", "count": 0, "projects": []}

    route_registry = MagicMock()
    route_registry.fast_path_callable.return_value = get_projects

    state = {
        "payload": {"__fast_path__": True},
        "selected": {
            "operation_id": "portfolio_plugin__get_projects",
            "plugin_id": "portfolio_plugin",
        },
        # Context-resolved + step-seeded junk the tool does not accept.
        "resolved_args": {"audience": "peer", "projectId": "seeded-from-env"},
        "plan": [
            {
                "operation_id": "portfolio_plugin__get_projects",
                "args": {"projectId": "from-step-args", "audience": "peer"},
            }
        ],
        "current_step_index": 0,
    }

    out = await _execute_single(
        step=state["plan"][0],
        step_results=[],
        state=state,
        route_registry=route_registry,
        llm=None,
        context_params={},
        api_url="",
    )

    assert out["response"]["status"] == "ok"
    assert "projectId" not in received["kwargs"]
    assert received["kwargs"]["audience"] == "peer"
    # Must not surface the old need_input TypeError path.
    assert out["response"].get("message") is None or "projectId" not in str(
        out["response"].get("message", "")
    )


@pytest.mark.asyncio
async def test_fast_path_keeps_known_params_only():
    """Unknown keys dropped; declared params preserved."""
    seen = {}

    async def tool(query: str, k: int = 3):
        seen["query"] = query
        seen["k"] = k
        return {"status": "ok"}

    route_registry = MagicMock()
    route_registry.fast_path_callable.return_value = tool

    state = {
        "payload": {"__fast_path__": True},
        "selected": {"operation_id": "get_star_stories", "plugin_id": "portfolio_plugin"},
        "resolved_args": {"query": "architecture", "k": 5, "projectId": "x", "tenant_id": 1},
        "plan": [{"operation_id": "get_star_stories", "args": {"projectId": "y"}}],
        "current_step_index": 0,
    }

    out = await _execute_single(
        step=state["plan"][0],
        step_results=[],
        state=state,
        route_registry=route_registry,
        llm=None,
        context_params={},
        api_url="",
    )
    assert out["response"]["status"] == "ok"
    assert seen == {"query": "architecture", "k": 5}


@pytest.mark.asyncio
async def test_dynamic_http_branch_fails_closed_on_auth_error_dict():
    """get_auth_headers() degrades to {"status": "error", ...} instead of raising
    (core.plugin_loader.plugin_auth_registry) — that dict must never be forwarded
    as literal HTTP headers to the real upstream request."""
    route_registry = MagicMock()

    state = {
        "payload": {
            "url": "https://example.invalid/api/thing",
            "method": "GET",
            "plugin_id": "some_plugin",
        },
        "selected": {"plugin_id": "some_plugin"},
        "resolved_args": {},
        "plan": [{"operation_id": "x", "args": {}}],
        "current_step_index": 0,
    }

    mock_registry = MagicMock()
    mock_registry.get_auth_headers = AsyncMock(
        return_value={"status": "error", "error": "circular_auth_delegation"}
    )

    with patch("core_graph.node.execute_step.get_registry", return_value=mock_registry), \
         patch("core_graph.node.execute_step.requests.request") as mock_request:
        out = await _execute_single(
            step=state["plan"][0],
            step_results=[],
            state=state,
            route_registry=route_registry,
            llm=None,
            context_params={},
            api_url="",
        )

    mock_request.assert_not_called()
    assert out["response"]["status"] == "auth_required"
