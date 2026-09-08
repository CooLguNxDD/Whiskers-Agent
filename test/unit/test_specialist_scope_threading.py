"""Specialist entry must thread scopes and fail closed for anonymous HTTP."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core_graph.agent_loop.runner import _authorize_fast_path
from core_graph.node.specialist_entry import make_specialist_entry_node


class _FakePrincipal:
    def __init__(self, scopes):
        self.scopes = scopes


@pytest.mark.asyncio
async def test_specialist_entry_anonymous_http_passes_empty_scopes():
    """No principal + non-stdio transport → caller_scopes=[] (not None/LOCAL_CLI)."""
    captured = {}

    async def fake_run_specialist(goal, **kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "summary": "done",
            "specialist_domain": "generic",
            "goal_class": "discover",
        }

    node = make_specialist_entry_node(MagicMock())
    with patch(
        "core_graph.subgraphs.specialist.registry.run_specialist",
        new=AsyncMock(side_effect=fake_run_specialist),
    ), patch(
        "core.scope_management.get_request_principal",
        return_value=None,
    ), patch(
        "core.context.transport.is_local_stdio",
        return_value=False,
    ), patch(
        "core.context.current_tenant_id",
    ) as tid:
        tid.get.return_value = 1
        out = await node({"user_query": "discover contacts", "session_id": "s1"})

    assert out.get("response") is not None
    assert captured.get("caller_scopes") == []


@pytest.mark.asyncio
async def test_specialist_entry_stdio_allows_none_scopes():
    """Local stdio without principal keeps unrestricted CLI scopes (None)."""
    captured = {}

    async def fake_run_specialist(goal, **kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "summary": "done",
            "specialist_domain": "generic",
            "goal_class": "discover",
        }

    node = make_specialist_entry_node(MagicMock())
    with patch(
        "core_graph.subgraphs.specialist.registry.run_specialist",
        new=AsyncMock(side_effect=fake_run_specialist),
    ), patch(
        "core.scope_management.get_request_principal",
        return_value=None,
    ), patch(
        "core.context.transport.is_local_stdio",
        return_value=True,
    ), patch(
        "core.context.current_tenant_id",
    ) as tid:
        tid.get.return_value = 1
        await node({"user_query": "list tools", "session_id": "s1"})

    assert captured.get("caller_scopes") is None


@pytest.mark.asyncio
async def test_specialist_entry_threads_principal_scopes():
    captured = {}

    async def fake_run_specialist(goal, **kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "summary": "done",
            "specialist_domain": "generic",
            "goal_class": "discover",
        }

    node = make_specialist_entry_node(MagicMock())
    with patch(
        "core_graph.subgraphs.specialist.registry.run_specialist",
        new=AsyncMock(side_effect=fake_run_specialist),
    ), patch(
        "core.scope_management.get_request_principal",
        return_value=_FakePrincipal(["plugin:portfolio_plugin"]),
    ), patch(
        "core.context.transport.is_local_stdio",
        return_value=False,
    ), patch(
        "core.context.current_tenant_id",
    ) as tid:
        tid.get.return_value = 1
        await node({"user_query": "bake portfolio", "session_id": "s1"})

    assert captured.get("caller_scopes") == ["plugin:portfolio_plugin"]


@pytest.mark.asyncio
async def test_specialist_entry_unwraps_catportfolio_ask():
    """Visitor wrapper seeds the flow with the sentence + page skeleton."""
    captured = {}

    async def fake_run_specialist(goal, **kwargs):
        captured["goal"] = goal
        captured.update(kwargs)
        return {
            "status": "ok",
            "summary": "spawned",
            "specialist_domain": "portfolio",
            "flow_id": "portfolio_ask_v1",
            "blocks": [{"type": "fishTank", "id": "fish-tank-1"}],
        }

    wrapped = (
        "You are a helpful portfolio assistant.\n\n---\n\n"
        "Visitor Question: tell me about your game project\n\n"
        '[System: CatPortfolio ask turn (goal_class=scoped_ask). '
        'Page context: {"view":"tank","tank_slugs":["whiskers-ai"],'
        '"block_index":[{"id":"fish-tank-1","type":"fishTank"}]}]'
    )
    node = make_specialist_entry_node(MagicMock())
    with patch(
        "core_graph.subgraphs.specialist.registry.run_specialist",
        new=AsyncMock(side_effect=fake_run_specialist),
    ), patch(
        "core.scope_management.get_request_principal",
        return_value=_FakePrincipal(["all"]),
    ), patch(
        "core.context.transport.is_local_stdio",
        return_value=False,
    ), patch(
        "core.context.current_tenant_id",
    ) as tid:
        tid.get.return_value = 1
        out = await node({"user_query": wrapped, "session_id": "s1"})

    assert captured["goal"] == "tell me about your game project"
    assert captured["goal_class"] == "scoped_ask"
    assert captured["plugin_context"]["tank_slugs"] == ["whiskers-ai"]
    assert out["response"]["flow_id"] == "portfolio_ask_v1"
    assert out["goal_loop_decision"] == "done"


@pytest.mark.asyncio
async def test_specialist_entry_state_scopes_used_when_principal_unset():
    """Regression: MCP HTTP token scopes in state["caller_scopes"] must reach
    the flow even though the request-principal contextvar is never set on
    that transport (it's only set by api/playground_routes.py)."""
    captured = {}

    async def fake_run_specialist(goal, **kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "summary": "done",
            "specialist_domain": "generic",
            "goal_class": "discover",
        }

    node = make_specialist_entry_node(MagicMock())
    with patch(
        "core_graph.subgraphs.specialist.registry.run_specialist",
        new=AsyncMock(side_effect=fake_run_specialist),
    ), patch(
        "core.scope_management.get_request_principal",
        return_value=None,
    ), patch(
        "core.context.transport.is_local_stdio",
        return_value=False,
    ), patch(
        "core.context.current_tenant_id",
    ) as tid:
        tid.get.return_value = 1
        out = await node(
            {
                "user_query": "tell me about your game project",
                "session_id": "s1",
                "caller_scopes": ["group:portfolio_plugin:ask"],
            }
        )

    assert out.get("response") is not None
    assert captured.get("caller_scopes") == ["group:portfolio_plugin:ask"]


@pytest.mark.asyncio
async def test_specialist_entry_state_scopes_win_over_principal():
    """State scopes take precedence even when a principal is also present."""
    captured = {}

    async def fake_run_specialist(goal, **kwargs):
        captured.update(kwargs)
        return {"status": "ok", "summary": "done", "specialist_domain": "generic"}

    node = make_specialist_entry_node(MagicMock())
    with patch(
        "core_graph.subgraphs.specialist.registry.run_specialist",
        new=AsyncMock(side_effect=fake_run_specialist),
    ), patch(
        "core.scope_management.get_request_principal",
        return_value=_FakePrincipal(["all"]),
    ), patch(
        "core.context.transport.is_local_stdio",
        return_value=False,
    ), patch(
        "core.context.current_tenant_id",
    ) as tid:
        tid.get.return_value = 1
        await node(
            {
                "user_query": "discover contacts",
                "session_id": "s1",
                "caller_scopes": ["group:portfolio_plugin:ask"],
            }
        )

    assert captured.get("caller_scopes") == ["group:portfolio_plugin:ask"]


@pytest.mark.asyncio
async def test_specialist_entry_none_state_scopes_still_fails_closed():
    """state["caller_scopes"] explicitly None (unauthenticated) must still
    fall through to the anonymous-HTTP fail-closed branch, not be treated as
    'no state available'."""
    captured = {}

    async def fake_run_specialist(goal, **kwargs):
        captured.update(kwargs)
        return {"status": "ok", "summary": "done", "specialist_domain": "generic"}

    node = make_specialist_entry_node(MagicMock())
    with patch(
        "core_graph.subgraphs.specialist.registry.run_specialist",
        new=AsyncMock(side_effect=fake_run_specialist),
    ), patch(
        "core.scope_management.get_request_principal",
        return_value=None,
    ), patch(
        "core.context.transport.is_local_stdio",
        return_value=False,
    ), patch(
        "core.context.current_tenant_id",
    ) as tid:
        tid.get.return_value = 1
        await node(
            {
                "user_query": "discover contacts",
                "session_id": "s1",
                "caller_scopes": None,
            }
        )

    assert captured.get("caller_scopes") == []


@pytest.mark.asyncio
async def test_specialist_entry_none_state_scopes_stdio_unrestricted():
    """state["caller_scopes"] is None + local stdio → unrestricted (None),
    same as when the key is absent entirely."""
    captured = {}

    async def fake_run_specialist(goal, **kwargs):
        captured.update(kwargs)
        return {"status": "ok", "summary": "done", "specialist_domain": "generic"}

    node = make_specialist_entry_node(MagicMock())
    with patch(
        "core_graph.subgraphs.specialist.registry.run_specialist",
        new=AsyncMock(side_effect=fake_run_specialist),
    ), patch(
        "core.scope_management.get_request_principal",
        return_value=None,
    ), patch(
        "core.context.transport.is_local_stdio",
        return_value=True,
    ), patch(
        "core.context.current_tenant_id",
    ) as tid:
        tid.get.return_value = 1
        await node(
            {
                "user_query": "list tools",
                "session_id": "s1",
                "caller_scopes": None,
            }
        )

    assert captured.get("caller_scopes") is None


def test_authorize_fast_path_denies_write_with_empty_scopes():
    """Empty scopes (anonymous specialist) must not authorize a write op."""

    class Op:
        plugin_id = "portfolio_plugin"
        operation_id = "portfolio_plugin__ingest_portfolio_context"
        required_scopes = {"plugin:portfolio_plugin"}
        tags = {"portfolio_plugin", "write"}

    catalog = MagicMock()
    catalog.get.return_value = Op()

    with patch(
        "core.route_registry.operation_catalog.get_operation_catalog",
        return_value=catalog,
    ):
        deny = _authorize_fast_path(
            "portfolio_plugin",
            "portfolio_plugin__ingest_portfolio_context",
            [],  # empty — not LOCAL_CLI
        )
    assert deny is not None
    assert "scope" in deny.lower() or "lack" in deny.lower()
