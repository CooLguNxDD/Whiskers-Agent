"""Unit tests for tool state routes and metadata exposure."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from api.tool_routes import set_tool_state, set_tools_batch_state
from api.plugin_routes import get_plugin_tools


routes = [
    Route("/api/plugins/session_gated/{plugin_id}/tools/{tool_name}/state", set_tool_state, methods=["POST"]),
    Route("/api/plugins/session_gated/{plugin_id}/tools/batch-state", set_tools_batch_state, methods=["POST"]),
    Route("/api/plugins/session_gated/{plugin_id}/tools", get_plugin_tools, methods=["GET"]),
]

app = Starlette(routes=routes)


@pytest.mark.asyncio
async def test_set_tool_state_routes(monkeypatch):
    """Test that the unified /state route calls correct backend functions for each state."""
    # Mock DB functions
    mock_set_tool_enabled = AsyncMock()
    mock_set_tool_hidden = AsyncMock()
    monkeypatch.setattr("api.tool_routes.set_tool_enabled", mock_set_tool_enabled)
    monkeypatch.setattr("api.tool_routes.set_tool_hidden", mock_set_tool_hidden)

    # Mock tool_visibility methods
    mock_show = MagicMock()
    mock_hide = MagicMock()
    mock_show_gateway = MagicMock()
    mock_hide_gateway = MagicMock()
    monkeypatch.setattr("api.tool_routes.tool_visibility.show", mock_show)
    monkeypatch.setattr("api.tool_routes.tool_visibility.hide", mock_hide)
    monkeypatch.setattr("api.tool_routes.tool_visibility.show_gateway", mock_show_gateway)
    monkeypatch.setattr("api.tool_routes.tool_visibility.hide_gateway", mock_hide_gateway)

    # Mock reinitialize and refresh
    mock_reinit = AsyncMock()
    mock_get_registry = MagicMock()
    mock_get_registry.return_value.lifecycle.reinitialize_plugin = mock_reinit
    monkeypatch.setattr("core.plugin_loader.plugin_registry.get_registry", mock_get_registry)

    mock_refresh = AsyncMock()
    monkeypatch.setattr("api.tool_routes.refresh_tools_summary", mock_refresh)

    client = TestClient(app)

    # Case 1: ENABLED state
    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/my_tool/state",
        json={"state": "enabled"}
    )
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["state"] == "enabled"
    assert res_data["is_enabled"] is True
    assert res_data["is_hidden"] is False

    mock_set_tool_enabled.assert_called_with("my_plugin", "my_tool", True)
    mock_set_tool_hidden.assert_called_with("my_plugin", "my_tool", False)
    mock_show.assert_called_with("my_tool")
    mock_show_gateway.assert_called_with("my_tool")
    mock_reinit.assert_called_with("my_plugin")
    assert mock_refresh.call_count == 1

    # Case 2: HIDDEN state
    mock_set_tool_enabled.reset_mock()
    mock_set_tool_hidden.reset_mock()
    mock_show.reset_mock()
    mock_hide_gateway.reset_mock()
    mock_reinit.reset_mock()
    mock_refresh.reset_mock()

    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/my_tool/state",
        json={"state": "hidden"}
    )
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["state"] == "hidden"
    assert res_data["is_enabled"] is True
    assert res_data["is_hidden"] is True

    mock_set_tool_enabled.assert_called_with("my_plugin", "my_tool", True)
    mock_set_tool_hidden.assert_called_with("my_plugin", "my_tool", True)
    mock_show.assert_called_with("my_tool")
    mock_hide_gateway.assert_called_with("my_tool")
    mock_reinit.assert_called_with("my_plugin")
    assert mock_refresh.call_count == 1

    # Case 3: DISABLED state
    mock_set_tool_enabled.reset_mock()
    mock_set_tool_hidden.reset_mock()
    mock_hide.reset_mock()
    mock_reinit.reset_mock()
    mock_refresh.reset_mock()

    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/my_tool/state",
        json={"state": "disabled"}
    )
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["state"] == "disabled"
    assert res_data["is_enabled"] is False
    assert res_data["is_hidden"] is False

    mock_set_tool_enabled.assert_called_with("my_plugin", "my_tool", False)
    mock_set_tool_hidden.assert_called_with("my_plugin", "my_tool", False)
    mock_hide.assert_called_with("my_tool")
    mock_reinit.assert_called_with("my_plugin")
    assert mock_refresh.call_count == 1

    # Invalid state
    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/my_tool/state",
        json={"state": "invalid_value"}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_plugin_tools_groups_and_access(monkeypatch):
    """Test that get_plugin_tools exposes tags category (group) and method operation class (access)."""
    # Mock DB record for plugin
    mock_record = MagicMock()
    mock_record.capabilities = ["tool_c"]
    mock_db_registry = AsyncMock()
    mock_db_registry.get_by_id.return_value = mock_record
    # get_plugin_tools lives in the api.plugin_routes.tools submodule (package
    # split) — patch the submodule-bound name, matching the convention the
    # rest of this suite uses (see test_plugin_routes_stale.py). Patching the
    # package-level re-export (api.plugin_routes._db_registry) doesn't
    # propagate: tools.py imports its own frozen `_db_registry` reference at
    # module-import time.
    monkeypatch.setattr("api.plugin_routes.tools._db_registry", mock_db_registry)

    # Mock DB tool config queries
    monkeypatch.setattr("db_layer.tool_config_store.get_tool_states", AsyncMock(return_value={"tool_a": True, "tool_b": True}))
    monkeypatch.setattr("db_layer.tool_config_store.get_tool_hidden_states", AsyncMock(return_value={"tool_a": False, "tool_b": True}))
    monkeypatch.setattr("db_layer.tool_config_store.get_tool_embedding_models", AsyncMock(return_value={}))
    monkeypatch.setattr("db_layer.permission_store.get_plugin_permissions", AsyncMock(return_value={}))

    # Mock routes registry with descriptors carrying tags/methods
    class MockRoute:
        def __init__(self, operation_id, method, tags=(), meta=None):
            self.operation_id = operation_id
            self.method = method
            self.tags = tags
            self.meta = meta
            self.description = f"Desc for {operation_id}"

    routes = [
        MockRoute("my_plugin__tool_a", "GET", tags=("records",)),
        MockRoute("my_plugin__tool_b", "POST", meta=MagicMock(category="messages")),
        MockRoute("my_plugin__tool_c", "GET"),
    ]

    mock_registry = MagicMock()
    mock_registry.route_registry.routes_for_plugin.return_value = routes
    monkeypatch.setattr("api.plugin_routes.tools.get_registry", lambda: mock_registry)

    client = TestClient(app)
    response = client.get("/api/plugins/session_gated/my_plugin/tools")
    assert response.status_code == 200
    data = response.json()
    assert "tools" in data

    tools = {t["name"]: t for t in data["tools"]}
    assert "tool_a" in tools
    assert "tool_b" in tools
    assert "tool_c" in tools

    # Check group & access
    assert tools["tool_a"]["group"] == "records"
    assert tools["tool_a"]["access"] == "read"

    assert tools["tool_b"]["group"] == "messages"
    assert tools["tool_b"]["access"] == "write"

    assert tools["tool_c"]["group"] == "general"
    assert tools["tool_c"]["access"] == "read"


@pytest.mark.asyncio
async def test_set_tools_batch_state_routes(monkeypatch):
    """Test that the batch state and/or permission route updates multiple tools correctly."""
    # Mock DB functions
    mock_set_tool_enabled = AsyncMock()
    mock_set_tool_hidden = AsyncMock()
    mock_set_permission = AsyncMock(return_value={"allow_read": True, "allow_write": True, "require_confirmation": False})

    monkeypatch.setattr("api.tool_routes.set_tool_enabled", mock_set_tool_enabled)
    monkeypatch.setattr("api.tool_routes.set_tool_hidden", mock_set_tool_hidden)
    monkeypatch.setattr("api.tool_routes.set_permission", mock_set_permission)

    # Mock tool_visibility methods
    mock_show = MagicMock()
    mock_hide = MagicMock()
    mock_show_gateway = MagicMock()
    mock_hide_gateway = MagicMock()
    monkeypatch.setattr("api.tool_routes.tool_visibility.show", mock_show)
    monkeypatch.setattr("api.tool_routes.tool_visibility.hide", mock_hide)
    monkeypatch.setattr("api.tool_routes.tool_visibility.show_gateway", mock_show_gateway)
    monkeypatch.setattr("api.tool_routes.tool_visibility.hide_gateway", mock_hide_gateway)

    # Mock reinitialize and refresh
    mock_reinit = AsyncMock()
    mock_get_registry = MagicMock()
    mock_get_registry.return_value.lifecycle.reinitialize_plugin = mock_reinit
    monkeypatch.setattr("core.plugin_loader.plugin_registry.get_registry", mock_get_registry)

    mock_refresh = AsyncMock()
    monkeypatch.setattr("api.tool_routes.refresh_tools_summary", mock_refresh)

    client = TestClient(app)

    # (a) State-only batch over 2 tools: ENABLED
    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/batch-state",
        json={"tool_names": ["tool_a", "tool_b"], "state": "enabled"}
    )
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["plugin_id"] == "my_plugin"
    assert res_data["count"] == 2
    assert len(res_data["updated"]) == 2
    assert res_data["updated"][0]["tool_name"] == "tool_a"
    assert res_data["updated"][0]["state"] == "enabled"
    assert res_data["updated"][1]["tool_name"] == "tool_b"
    assert res_data["updated"][1]["state"] == "enabled"

    # Assert set_tool_enabled called twice (once for tool_a, once for tool_b)
    assert mock_set_tool_enabled.call_count == 2
    mock_set_tool_enabled.assert_any_call("my_plugin", "tool_a", True)
    mock_set_tool_enabled.assert_any_call("my_plugin", "tool_b", True)

    assert mock_set_tool_hidden.call_count == 2
    mock_set_tool_hidden.assert_any_call("my_plugin", "tool_a", False)
    mock_set_tool_hidden.assert_any_call("my_plugin", "tool_b", False)

    # reinitialize_plugin and refresh_tools_summary once each
    mock_reinit.assert_called_once_with("my_plugin")
    mock_refresh.assert_called_once()

    # Reset mocks for next case
    mock_set_tool_enabled.reset_mock()
    mock_set_tool_hidden.reset_mock()
    mock_reinit.reset_mock()
    mock_refresh.reset_mock()
    mock_set_permission.reset_mock()

    # (b) Permission-only batch over 2 tools
    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/batch-state",
        json={
            "tool_names": ["tool_a", "tool_b"],
            "permission": {"allow_read": True, "allow_write": False, "require_confirmation": False}
        }
    )
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["count"] == 2
    assert "permission" in res_data["updated"][0]

    assert mock_set_permission.call_count == 2
    mock_set_permission.assert_any_call(
        "my_plugin", "tool_a",
        allow_read=True, allow_write=False, require_confirmation=False
    )
    mock_set_permission.assert_any_call(
        "my_plugin", "tool_b",
        allow_read=True, allow_write=False, require_confirmation=False
    )

    # reinitialize_plugin and refresh_tools_summary once each
    mock_reinit.assert_called_once_with("my_plugin")
    mock_refresh.assert_called_once()

    # (c) Combined batch
    mock_set_tool_enabled.reset_mock()
    mock_set_tool_hidden.reset_mock()
    mock_reinit.reset_mock()
    mock_refresh.reset_mock()
    mock_set_permission.reset_mock()

    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/batch-state",
        json={
            "tool_names": ["tool_a"],
            "state": "hidden",
            "permission": {"allow_read": False}
        }
    )
    assert response.status_code == 200
    mock_set_tool_enabled.assert_called_once_with("my_plugin", "tool_a", True)
    mock_set_tool_hidden.assert_called_once_with("my_plugin", "tool_a", True)
    mock_set_permission.assert_called_once_with("my_plugin", "tool_a", allow_read=False)
    mock_reinit.assert_called_once_with("my_plugin")
    mock_refresh.assert_called_once()

    # (d) Error cases: empty tool_names
    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/batch-state",
        json={"tool_names": [], "state": "enabled"}
    )
    assert response.status_code == 400

    # missing state and permission
    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/batch-state",
        json={"tool_names": ["tool_a"]}
    )
    assert response.status_code == 400

    # invalid state
    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/batch-state",
        json={"tool_names": ["tool_a"], "state": "invalid_value"}
    )
    assert response.status_code == 400

    # invalid permission type
    response = client.post(
        "/api/plugins/session_gated/my_plugin/tools/batch-state",
        json={"tool_names": ["tool_a"], "permission": {"allow_read": "not_a_bool"}}
    )
    assert response.status_code == 400
