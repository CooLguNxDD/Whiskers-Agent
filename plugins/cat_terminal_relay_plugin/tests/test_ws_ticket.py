"""Unit tests for the console WS ticket helper (dev seam + OAuth-disabled guard)."""

import pytest

import plugins.cat_terminal_relay_plugin.services.ws_ticket as ws_ticket
from plugins.cat_terminal_relay_plugin.services.ws_ticket import mint_console_ticket


async def test_dev_seam_issues_dev_ticket(monkeypatch):
    # AuthService._require_svc reads core.context._oauth_svc directly (not
    # oauth_provider) — that's what must be None to raise AuthServiceUnavailable
    # and hit the dev seam.
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")
    monkeypatch.setattr("core.context._oauth_svc", None, raising=False)
    ticket = await mint_console_ticket("admin-console")
    assert ticket == "dev:admin-console:core:terminal:write"


async def test_oauth_disabled_without_dev_raises(monkeypatch):
    monkeypatch.delenv("CAT_TERMINAL_DEV_AUTH", raising=False)
    monkeypatch.setattr("core.context._oauth_svc", None, raising=False)
    with pytest.raises(RuntimeError):
        await mint_console_ticket("admin-console")


async def test_mints_rs256_via_oauth_service(monkeypatch):
    """When OAuth is enabled, the helper mints a core:terminal:write token via
    AuthService.mint_scoped_token (AuthService._require_svc reads
    core.context._oauth_svc directly, not oauth_provider._svc)."""
    calls = {}

    class _FakeSvc:
        async def ensure_keypair(self):
            return "kid-1"

        async def ensure_internal_client(self, client_id, scopes="whiskers"):
            calls["client_id"] = client_id
            calls["client_scopes"] = scopes

        async def _mint_jwt(self, kid, subject, client_id, scopes, ttl, token_type="access"):
            calls.update(kid=kid, subject=subject, scopes=scopes, ttl=ttl, token_type=token_type)
            return ("jwt-token", "jti", None)

    monkeypatch.setattr("core.context._oauth_svc", _FakeSvc(), raising=False)
    token = await mint_console_ticket("admin-console")
    assert token == "jwt-token"
    assert calls["subject"] == "admin-console"
    assert calls["scopes"] == ["core:terminal:write"]
    assert calls["ttl"] == ws_ticket._TICKET_TTL_SECONDS
