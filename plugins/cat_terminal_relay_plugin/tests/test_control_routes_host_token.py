import json as _json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from starlette.responses import JSONResponse
from plugins.cat_terminal_relay_plugin.routes import control_routes as cr
from utils.server_config import TERMINAL_HOST_TOKEN_TTL_SECONDS


class FakeRequest:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}


@pytest.mark.asyncio
async def test_host_token_unauthenticated(monkeypatch):
    # Mock _session_subject to return None (unauthenticated)
    async def mock_session_subject(request):
        return None

    monkeypatch.setattr(cr, "_session_subject", mock_session_subject)

    req = FakeRequest()
    resp = await cr.host_token(req)
    assert resp.status_code == 401

    data = _json.loads(resp.body)
    assert data["error"] == "unauthenticated"


@pytest.mark.asyncio
async def test_host_token_oauth_disabled(monkeypatch):
    # Mock _session_subject to return a subject
    async def mock_session_subject(request):
        return "alice"

    monkeypatch.setattr(cr, "_session_subject", mock_session_subject)

    # AuthService._require_svc reads core.context._oauth_svc directly (not
    # oauth_provider) — that's the None check that raises AuthServiceUnavailable.
    monkeypatch.setattr("core.context._oauth_svc", None)

    req = FakeRequest()
    resp = await cr.host_token(req)
    assert resp.status_code == 503

    data = _json.loads(resp.body)
    assert data["status"] == "error"
    assert data["error"] == "oauth_disabled"


@pytest.mark.asyncio
async def test_host_token_success(monkeypatch):
    # Mock _session_subject to return a subject
    async def mock_session_subject(request):
        return "alice"

    monkeypatch.setattr(cr, "_session_subject", mock_session_subject)
    from plugins.cat_terminal_relay_plugin.plugin_config import SETTINGS
    monkeypatch.setitem(SETTINGS, "terminal_host_token_ttl_seconds", TERMINAL_HOST_TOKEN_TTL_SECONDS)

    # Setup mocked OAuthService — AuthService._require_svc reads
    # core.context._oauth_svc directly, not oauth_provider._svc.
    mock_svc = MagicMock()

    mock_svc.ensure_internal_client = AsyncMock()
    mock_svc.ensure_keypair = AsyncMock(return_value="mocked-kid")

    mock_expires_at = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
    mock_svc._mint_jwt = AsyncMock(return_value=("mocked-token-xyz", "mocked-jti", mock_expires_at))

    monkeypatch.setattr("core.context._oauth_svc", mock_svc)

    req = FakeRequest()
    resp = await cr.host_token(req)
    assert resp.status_code == 200

    data = _json.loads(resp.body)
    assert data["status"] == "ok"
    assert data["token"] == "mocked-token-xyz"
    assert data["expires_in"] == TERMINAL_HOST_TOKEN_TTL_SECONDS
    assert data["expires_at"] == mock_expires_at.isoformat()

    # Verify calls on mocked OAuth service
    mock_svc.ensure_internal_client.assert_awaited_once_with(
        "whiskers-host",
        scopes="core:terminal:read"
    )
    mock_svc.ensure_keypair.assert_awaited_once()
    mock_svc._mint_jwt.assert_awaited_once_with(
        "mocked-kid",
        subject="alice",
        client_id="whiskers-host",
        scopes=["core:terminal:read"],
        ttl=TERMINAL_HOST_TOKEN_TTL_SECONDS,
        token_type="access"
    )


@pytest.mark.asyncio
async def test_host_token_unexpected_exception(monkeypatch):
    # Mock _session_subject to raise an unexpected exception
    async def mock_session_subject(request):
        raise RuntimeError("database crash")

    monkeypatch.setattr(cr, "_session_subject", mock_session_subject)

    req = FakeRequest()
    resp = await cr.host_token(req)
    assert resp.status_code == 500

    data = _json.loads(resp.body)
    assert data["status"] == "error"
    assert data["error"] == "mint_failed"
