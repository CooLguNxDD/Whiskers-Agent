"""Bug B: OAuth MCP tokens inherit session role via pending + union merge."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_complete_authorization_unions_operator_role():
    """pending.ocat_role=operator → scopes include operator playground scopes even with extra_scopes=[]."""
    from oauth.oauth_service import OAuthService_FastMCPProvider

    svc = MagicMock()
    svc.pop_pending_auth = AsyncMock(return_value=None)
    svc.create_auth_code = AsyncMock(return_value="authcode123")
    svc.save_pending_auth = AsyncMock()

    provider = OAuthService_FastMCPProvider.__new__(OAuthService_FastMCPProvider)
    provider._svc = svc
    provider._pending_auths = {
        "state1": {
            "client_id": "mcp-client",
            "code_challenge": "ch",
            "code_challenge_method": "S256",
            "redirect_uri": "http://localhost/cb",
            "scopes": ["whiskers"],
            "state": "mcpstate",
            "ocat_role": "operator",
            "ocat_user_id": "user-1",
            "ocat_tenant": 2,
        }
    }
    provider._pending_ts = {"state1": 0}
    provider._auth_codes = {}
    provider._auth_code_claims = {}

    op_scopes = ["plugin:fake_plugin", "group:fake_plugin:read"]
    with patch(
        "core.api_key_management.scopes.playground_mcp_scopes",
        return_value=op_scopes,
    ):
        url = await provider._complete_authorization("state1", "whiskers_core", extra_scopes=[])

    assert "code=" in url
    # Auth code stored with union scopes
    code = list(provider._auth_codes.keys())[0]
    stored = provider._auth_codes[code]
    scopes = list(stored.scopes)
    assert "whiskers" in scopes
    for s in op_scopes:
        assert s in scopes
    # Claims carry role and tenant
    assert provider._auth_code_claims[code]["ocat_role"] == "operator"
    assert provider._auth_code_claims[code]["ocat_tenant"] == 2


@pytest.mark.asyncio
async def test_complete_authorization_no_role_keeps_client_scopes():
    from oauth.oauth_service import OAuthService_FastMCPProvider

    svc = MagicMock()
    svc.pop_pending_auth = AsyncMock(return_value=None)
    svc.create_auth_code = AsyncMock(return_value="authcode456")

    provider = OAuthService_FastMCPProvider.__new__(OAuthService_FastMCPProvider)
    provider._svc = svc
    provider._pending_auths = {
        "state2": {
            "client_id": "mcp-client",
            "code_challenge": "ch",
            "code_challenge_method": "S256",
            "redirect_uri": "http://localhost/cb",
            "scopes": ["whiskers"],
            "state": "mcpstate",
        }
    }
    provider._pending_ts = {"state2": 0}
    provider._auth_codes = {}

    await provider._complete_authorization("state2", "whiskers_core")
    code = list(provider._auth_codes.keys())[0]
    assert list(provider._auth_codes[code].scopes) == ["whiskers"]
    assert not getattr(provider, "_auth_code_claims", {}).get(code)


@pytest.mark.asyncio
async def test_oauth_complete_layer1_viewer_not_admin():
    """oauth_complete_layer1 uses playground_mcp_scopes(role), not blanket admin."""
    import oauth.oauth_routes as oroutes

    request = MagicMock()
    request.path_params = {"auth_state": "st"}
    request.cookies = {"session": "tok"}

    mock_provider = MagicMock()
    mock_provider._complete_authorization = AsyncMock(
        return_value="http://localhost/cb?code=x&state=y"
    )
    mock_svc = MagicMock()
    mock_svc.validate_token = AsyncMock(
        return_value={"ocat_role": "viewer", "sub": "u1", "ocat_tenant": 99}
    )
    mock_provider._svc = mock_svc
    mock_provider.attach_session_role_to_pending = MagicMock()

    viewer_scopes = ["group:fake_plugin:read"]
    with patch("oauth.oauth_routes.oauth_provider", mock_provider), \
         patch(
             "core.api_key_management.scopes.playground_mcp_scopes",
             return_value=viewer_scopes,
         ):
        res = await oroutes.oauth_complete_layer1(request)

    assert res.status_code == 303
    mock_provider.attach_session_role_to_pending.assert_called_once_with(
        "st", "viewer", "u1", 99
    )
    extra = mock_provider._complete_authorization.call_args.kwargs.get("extra_scopes")
    assert extra == viewer_scopes
    assert "admin" not in (extra or [])
