"""Unit tests for direct octk_ API-key auth in the terminal relay handshake
and MCP-tool caller identity resolution.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_authenticate_handshake_api_key_valid(monkeypatch):
    from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

    async def mock_lookup(token):
        assert token == "octk_good"
        return {"key_id": "ak_1", "subject": "alice", "scopes": ["terminal:use"]}

    monkeypatch.setattr("core.api_key_management.store.lookup_active_by_token", mock_lookup)

    ws = MagicMock()
    ws.query_params = {"token": "octk_good"}
    ws.headers = {}

    subject = await auth_mod.authenticate_handshake(ws, "terminal:use")
    assert subject == "alice"


@pytest.mark.asyncio
async def test_authenticate_handshake_api_key_write_implies_read(monkeypatch):
    """terminal:use maps to core:terminal:write, which now implies
    core:terminal:read (== terminal:host) via grammar.expand_implied — the
    grant satisfies the read-only requirement too. (Previously denied: the
    handshake's inline scope check didn't apply grammar implication.)
    """
    from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

    async def mock_lookup(token):
        return {"key_id": "ak_1", "subject": "alice", "scopes": ["terminal:use"]}

    monkeypatch.setattr("core.api_key_management.store.lookup_active_by_token", mock_lookup)

    ws = MagicMock()
    ws.query_params = {"token": "octk_good"}
    ws.headers = {}

    subject = await auth_mod.authenticate_handshake(ws, "terminal:host")
    assert subject == "alice"


@pytest.mark.asyncio
async def test_authenticate_handshake_api_key_missing_scope(monkeypatch):
    """An unrelated scope (e.g. graph access) still denies terminal access."""
    from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

    async def mock_lookup(token):
        return {"key_id": "ak_1", "subject": "alice", "scopes": ["core:graph:read"]}

    monkeypatch.setattr("core.api_key_management.store.lookup_active_by_token", mock_lookup)

    ws = MagicMock()
    ws.query_params = {"token": "octk_good"}
    ws.headers = {}

    subject = await auth_mod.authenticate_handshake(ws, "terminal:host")
    assert subject is None


@pytest.mark.asyncio
async def test_authenticate_handshake_api_key_migrated_scope_still_works(monkeypatch):
    """core_047_scope_cutover rewrites a key's ``terminal:use`` to
    ``core:terminal:write`` in the DB — the handshake must still accept it
    for a route whose REQUIRED_SCOPE constant is still the legacy string
    (the terminal relay's raw checks are deliberately not migrated off that
    constant this branch; the alias widening in auth.py/roles.py carries the
    load instead)."""
    from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

    async def mock_lookup(token):
        return {"key_id": "ak_1", "subject": "alice", "scopes": ["core:terminal:write"]}

    monkeypatch.setattr("core.api_key_management.store.lookup_active_by_token", mock_lookup)

    ws = MagicMock()
    ws.query_params = {"token": "octk_good"}
    ws.headers = {}

    assert await auth_mod.authenticate_handshake(ws, "terminal:use") == "alice"


@pytest.mark.asyncio
async def test_authenticate_handshake_api_key_unknown(monkeypatch):
    from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

    async def mock_lookup(token):
        return None

    monkeypatch.setattr("core.api_key_management.store.lookup_active_by_token", mock_lookup)

    ws = MagicMock()
    ws.query_params = {"token": "octk_bad"}
    ws.headers = {}

    subject = await auth_mod.authenticate_handshake(ws, "terminal:use")
    assert subject is None


@pytest.mark.asyncio
async def test_terminal_tools_caller_identity_api_key(monkeypatch):
    from plugins.cat_terminal_relay_plugin.MCPTools import terminal_tools as tt

    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_headers",
        lambda: {"authorization": "Bearer octk_good"},
    )

    async def mock_lookup(token):
        return {"key_id": "ak_2", "subject": "bob", "scopes": ["terminal:use", "terminal:host"]}

    monkeypatch.setattr("core.api_key_management.store.lookup_active_by_token", mock_lookup)

    subject, scopes = await tt._caller_identity()
    assert subject == "bob"
    assert scopes == {"terminal:use", "terminal:host"}


@pytest.mark.asyncio
async def test_sandbox_tools_caller_identity_api_key(monkeypatch):
    from plugins.cat_terminal_relay_plugin.MCPTools import sandbox_tools as st

    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_headers",
        lambda: {"authorization": "Bearer octk_good"},
    )

    async def mock_lookup(token):
        return {"key_id": "ak_3", "subject": "carol", "scopes": ["terminal:use"]}

    monkeypatch.setattr("core.api_key_management.store.lookup_active_by_token", mock_lookup)

    subject, scopes = await st._caller_identity()
    assert subject == "carol"
    assert scopes == {"terminal:use"}


# ---------------------------------------------------------------------------
# _has_required_scope — routed through evaluate_access (regression for the
# inline sentinel-check duplication flagged by the Jules review).
# ---------------------------------------------------------------------------


class TestHasRequiredScope:
    """``_has_required_scope`` must keep sentinel bypass and legacy-alias
    behaviour, and gain grammar implication it never had inline."""

    def test_sentinel_all_bypasses(self):
        from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

        assert auth_mod._has_required_scope({"all"}, "core:terminal:write") is True

    def test_sentinel_wildcard_bypasses(self):
        from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

        assert auth_mod._has_required_scope({"*"}, "core:terminal:write") is True

    def test_sentinel_admin_bypasses(self):
        from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

        assert auth_mod._has_required_scope({"admin"}, "core:terminal:write") is True

    def test_exact_token_allowed(self):
        from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

        assert auth_mod._has_required_scope({"core:terminal:write"}, "core:terminal:write") is True

    def test_legacy_alias_satisfies_new_token(self):
        from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

        assert auth_mod._has_required_scope({"terminal:use"}, "core:terminal:write") is True

    def test_new_token_satisfies_legacy_requirement(self):
        from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

        assert auth_mod._has_required_scope({"core:terminal:write"}, "terminal:use") is True

    def test_write_implies_read(self):
        """New behaviour: the old inline check missed grammar implication."""
        from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

        assert auth_mod._has_required_scope({"core:terminal:write"}, "core:terminal:read") is True

    def test_unrelated_scope_denied(self):
        from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

        assert auth_mod._has_required_scope({"core:graph:read"}, "core:terminal:write") is False

    def test_empty_scopes_denied(self):
        from plugins.cat_terminal_relay_plugin.routes import auth as auth_mod

        assert auth_mod._has_required_scope(set(), "core:terminal:write") is False
def test_throttled_warn_bounds():
    """Verify _throttled_warn bounds its internal dictionary size."""
    from plugins.cat_terminal_relay_plugin.routes.auth import _throttled_warn, _last_warned
    _last_warned.clear()
    for i in range(15000):
        _throttled_warn("test", f"token_{i}")

    assert len(_last_warned) <= 10000
    assert len(_last_warned) > 0
