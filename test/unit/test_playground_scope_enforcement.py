"""Playground REST paths pass role scopes into graph / deny invoke."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.requests import Request

import api.playground_routes as pr
from core.scope_management import PrincipalKind


def _make_request(method="POST", path="/api/playground/chat", body=None):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"content-type", b"application/json")],
    }
    req = Request(scope)

    async def _json():
        return body or {}

    req.json = _json  # type: ignore[method-assign]
    return req


@pytest.mark.asyncio
async def test_chat_passes_role_scopes():
    body = {"message": "hello", "session_id": "s1"}
    request = _make_request(body=body)
    captured = {}

    async def fake_run_graph(message, **kwargs):
        captured.update(kwargs)
        return {"status": "ok"}

    with patch.object(pr, "_resolve_principal_role", AsyncMock(return_value="viewer")), \
         patch("api.playground_routes.run_graph_impl", side_effect=fake_run_graph), \
         patch("core.api_key_management.scopes.playground_mcp_scopes", return_value=["group:core:read"]), \
         patch("core.api_key_management.scopes.resolve_force_execute", return_value=False):
        res = await pr.playground_chat(request)

    assert res.status_code == 200
    assert captured.get("caller_scopes") == ["group:core:read"]
    assert captured.get("caller_role") == "viewer"
    assert captured.get("caller_kind") == PrincipalKind.SESSION_USER


@pytest.mark.asyncio
async def test_invoke_viewer_write_returns_403(monkeypatch):
    monkeypatch.setattr(
        "core.scope_management.policy.scope_enforcement_enabled", lambda: True
    )
    monkeypatch.setattr(
        "core.scope_management.policy.get_enforcement_mode", lambda: "enforce"
    )
    body = {"tool": "create_record", "arguments": {}}
    request = _make_request(path="/api/playground/tools/invoke", body=body)

    with patch.object(pr, "_resolve_principal_role", AsyncMock(return_value="viewer")), \
         patch.object(pr, "_get_tool_plugin_mapping", AsyncMock(return_value={"create_record": "fake_plugin"})), \
         patch("core.api_key_management.scopes.playground_mcp_scopes", return_value=["group:fake_plugin:read"]), \
         patch("core.context._registries.route_registry") as reg:
        desc = MagicMock()
        desc.plugin_id = "fake_plugin"
        desc.tags = ("write",)
        reg.get.return_value = desc
        res = await pr.playground_invoke_tool(request)

    assert res.status_code == 403
    import json
    data = json.loads(res.body)
    assert data.get("error") == "scope_denied"


@pytest.mark.asyncio
async def test_invoke_master_read_allowed(monkeypatch):
    monkeypatch.setattr(
        "core.scope_management.policy.scope_enforcement_enabled", lambda: True
    )
    monkeypatch.setattr(
        "core.scope_management.policy.get_enforcement_mode", lambda: "enforce"
    )
    body = {"tool": "list_records", "arguments": {}}
    request = _make_request(path="/api/playground/tools/invoke", body=body)

    mock_result = MagicMock()
    mock_result.structured_content = {"ok": True}
    mock_result.content = []

    with patch.object(pr, "_resolve_principal_role", AsyncMock(return_value="master")), \
         patch.object(pr, "_get_tool_plugin_mapping", AsyncMock(return_value={"list_records": "fake_plugin"})), \
         patch("core.api_key_management.scopes.playground_mcp_scopes", return_value=["admin"]), \
         patch("core.context._registries.route_registry") as reg, \
         patch("api.playground_routes.mcp.call_tool", AsyncMock(return_value=mock_result)):
        desc = MagicMock()
        desc.plugin_id = "fake_plugin"
        desc.tags = ("read",)
        reg.get.return_value = desc
        res = await pr.playground_invoke_tool(request)

    assert res.status_code == 200
    import json
    data = json.loads(res.body)
    assert data.get("ok") is True
