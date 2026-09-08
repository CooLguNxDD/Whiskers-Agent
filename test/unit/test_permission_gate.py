"""
Unit tests for permission_gate node (Part C).
Covers: read allowed; write→confirmation_needed; write denied; force_execute bypass; operation_class.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core_graph.node.permission_gate import make_permission_gate_node
from core_graph.node.context import GraphRuntimeContext


@pytest.fixture(autouse=True)
def _enforcement_on(monkeypatch):
    monkeypatch.setattr(
        "core.scope_management.policy.scope_enforcement_enabled", lambda: True
    )
    monkeypatch.setattr(
        "core.scope_management.policy.get_enforcement_mode", lambda: "enforce"
    )


def _make_ctx():
    ctx = MagicMock(spec=GraphRuntimeContext)
    ctx.route_registry = None
    return ctx


@pytest.mark.asyncio
async def test_operation_class_maps_write_methods():
    from core_graph.node.helpers import operation_class, _WRITE_METHODS
    assert operation_class({"method": "POST"}) == "write"
    assert operation_class({"method": "GET"}) == "read"
    assert operation_class(None) == "read"


@pytest.mark.asyncio
async def test_read_allowed_passes_through():
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "p", "operation_id": "get_foo"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "GET"},
    }
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": False, "require_confirmation": True}
        out = await node(state)
        assert out == {}


@pytest.mark.asyncio
async def test_write_denied_when_not_allow_write():
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "p", "operation_id": "post_bar"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "POST"},
    }
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": False, "require_confirmation": True}
        out = await node(state)
        assert out.get("response", {}).get("status") == "error"
        assert "denied" in out.get("response", {}).get("message", "").lower()


@pytest.mark.asyncio
async def test_write_emits_confirmation_needed_when_require_and_not_force():
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "p", "operation_id": "put_x"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "PUT"},
        "force_execute": False,
    }
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": True, "require_confirmation": True}
        out = await node(state)
        assert out.get("response", {}).get("status") == "confirmation_needed"


@pytest.mark.asyncio
async def test_force_execute_bypasses_confirmation():
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "p", "operation_id": "delete_y"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "DELETE"},
        "force_execute": True,
    }
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": True, "require_confirmation": True}
        out = await node(state)
        assert out == {}


@pytest.mark.asyncio
async def test_goal_alone_does_not_bypass_write_confirmation():
    """goal is agentic objective, not user approval — still require confirmation."""
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "p", "operation_id": "delete_y"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "DELETE"},
        "force_execute": False,
        "goal": "delete all contacts",
    }
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": True, "require_confirmation": True}
        out = await node(state)
        assert out.get("response", {}).get("status") == "confirmation_needed"


@pytest.mark.asyncio
async def test_dangerously_skip_permissions_bypasses_all():
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "p", "operation_id": "delete_y"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "DELETE"},
    }
    with patch.dict("os.environ", {"DANGEROUSLY_SKIP_PERMISSIONS": "true", "WHISKERS_TRANSPORT": "stdio"}):
        out = await node(state)
        assert out == {}


@pytest.mark.asyncio
async def test_dangerously_skip_permissions_still_gates_with_http():
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "p", "operation_id": "delete_y"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "DELETE"},
    }
    with patch.dict("os.environ", {"DANGEROUSLY_SKIP_PERMISSIONS": "true", "WHISKERS_TRANSPORT": "http"}):
        with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
            gp.return_value = {"allow_read": True, "allow_write": False, "require_confirmation": True}
            out = await node(state)
            assert out.get("response", {}).get("status") == "error"
            assert "write access denied" in out.get("response", {}).get("message", "").lower()


@pytest.mark.asyncio
async def test_caller_scopes_none_skips_scope_gate():
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "my_plugin", "operation_id": "get_foo"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "GET"},
        "caller_scopes": None,
    }
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": True, "require_confirmation": True}
        out = await node(state)
        assert out == {}


@pytest.mark.asyncio
async def test_caller_scopes_empty_denies_when_plugin_resolvable():
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "my_plugin", "operation_id": "get_foo"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "GET"},
        "caller_scopes": [],
    }
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": True, "require_confirmation": True}
        out = await node(state)
        assert out.get("response", {}).get("status") == "error"
        assert "scope denied" in out.get("response", {}).get("message", "").lower()


@pytest.mark.asyncio
async def test_force_execute_does_not_bypass_scope_deny():
    """force_execute skips confirm UX only — ScopeManager still authorizes."""
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "my_plugin", "operation_id": "get_foo"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "GET"},
        "caller_scopes": [],
        "force_execute": True,
    }
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": True, "require_confirmation": True}
        out = await node(state)
        assert out.get("response", {}).get("status") == "error"
        assert "scope denied" in out.get("response", {}).get("message", "").lower()


@pytest.mark.asyncio
async def test_caller_scopes_plugin_token_allows():
    node = make_permission_gate_node(_make_ctx())
    state = {
        "plan": [{"plugin_id": "my_plugin", "operation_id": "get_foo"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "GET"},
        "caller_scopes": ["plugin:my_plugin"],
    }
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": True, "require_confirmation": True}
        out = await node(state)
        assert out == {}


def _proxy_gate_state(caller_scopes):
    return {
        "plan": [{"plugin_id": "proxy_X", "operation_id": "proxy_X__search"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "CALL"},
        "caller_scopes": caller_scopes,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_scopes", [
    ["plugin:proxy_X"],
    ["group:proxy_X:proxy"],
])
async def test_caller_scopes_proxy_token_allows(caller_scopes):
    ctx = _make_ctx()
    mock_desc = MagicMock()
    mock_desc.tags = ("proxy_X", "proxy")
    ctx.route_registry = MagicMock()
    ctx.route_registry.get.return_value = mock_desc
    node = make_permission_gate_node(ctx)
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": True, "require_confirmation": True}
        out = await node(_proxy_gate_state(caller_scopes))
        assert out == {}


@pytest.mark.asyncio
async def test_caller_scopes_floor_only_denies_proxy():
    ctx = _make_ctx()
    mock_desc = MagicMock()
    mock_desc.tags = ("proxy_X", "proxy")
    ctx.route_registry = MagicMock()
    ctx.route_registry.get.return_value = mock_desc
    node = make_permission_gate_node(ctx)
    with patch("db_layer.permission_store.get_permission", new_callable=AsyncMock) as gp:
        gp.return_value = {"allow_read": True, "allow_write": True, "require_confirmation": True}
        out = await node(_proxy_gate_state(["whiskers"]))
        assert out.get("response", {}).get("status") == "error"
        assert "scope denied" in out.get("response", {}).get("message", "").lower()

