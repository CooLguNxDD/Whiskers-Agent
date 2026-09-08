"""Unit tests for role-aware playground MCP token minting."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.requests import Request

import api.playground_routes as pr


def _make_request() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/playground/mcp-token",
        "headers": [],
    }
    return Request(scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "master"])
async def test_playground_mcp_token_admin_role_stamps_admin(role):
    """Admin/master session mints Bearer with admin scope for plugin gate bypass."""
    request = _make_request()
    mock_svc = MagicMock()
    mock_svc.ensure_internal_client = AsyncMock()
    mock_svc._issue_token_pair = AsyncMock(return_value={
        "access_token": "tok",
        "expires_in": 3600,
    })

    with patch("core.context.OAUTH_ENABLED", True), \
         patch("core.context.MCP_SERVER_URL", "http://localhost:8000"), \
         patch("core.context._oauth_svc", mock_svc), \
         patch.object(pr, "_resolve_principal_role", AsyncMock(return_value=role)):
        res = await pr.playground_mcp_token(request)

    assert res.status_code == 200
    issued_scopes = mock_svc._issue_token_pair.call_args[0][1]
    assert "admin" in issued_scopes


@pytest.mark.asyncio
async def test_playground_mcp_token_non_admin_uses_scopes_for_role():
    """Non-admin role receives config-driven scopes, not admin bypass."""
    request = _make_request()
    mock_svc = MagicMock()
    mock_svc.ensure_internal_client = AsyncMock()
    mock_svc._issue_token_pair = AsyncMock(return_value={
        "access_token": "tok",
        "expires_in": 3600,
    })
    role_scopes = ["whiskers", "plugin:fake_plugin"]

    with patch("core.context.OAUTH_ENABLED", True), \
         patch("core.context.MCP_SERVER_URL", "http://localhost:8000"), \
         patch("core.context._oauth_svc", mock_svc), \
         patch.object(pr, "_resolve_principal_role", AsyncMock(return_value="viewer")), \
         patch("core.api_key_management.scopes.playground_mcp_scopes", return_value=role_scopes):
        res = await pr.playground_mcp_token(request)

    assert res.status_code == 200
    issued_scopes = mock_svc._issue_token_pair.call_args[0][1]
    assert issued_scopes == role_scopes
    assert "admin" not in issued_scopes


@pytest.mark.asyncio
async def test_playground_mcp_token_unresolved_role_denies_all_scopes():
    """When role cannot be resolved, mint with deny-all scopes (no escalation)."""
    request = _make_request()
    mock_svc = MagicMock()
    mock_svc.ensure_internal_client = AsyncMock()
    mock_svc._issue_token_pair = AsyncMock(return_value={
        "access_token": "tok",
        "expires_in": 3600,
    })

    with patch("core.context.OAUTH_ENABLED", True), \
         patch("core.context.MCP_SERVER_URL", "http://localhost:8000"), \
         patch("core.context._oauth_svc", mock_svc), \
         patch.object(pr, "_resolve_principal_role", AsyncMock(return_value=None)), \
         patch("core.api_key_management.scopes.playground_mcp_scopes", return_value=[]):
        res = await pr.playground_mcp_token(request)

    assert res.status_code == 200
    issued_scopes = mock_svc._issue_token_pair.call_args[0][1]
    assert issued_scopes == []
    assert "admin" not in issued_scopes