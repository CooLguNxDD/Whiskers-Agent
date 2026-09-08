"""Unit tests for core.api_key_management.scopes and store scope persistence."""

import pytest
from sqlalchemy import delete

from db_layer.connection import get_async_session
from db_layer.models import ApiKey
from core.api_key_management.store import (
    create_api_key,
    lookup_active_by_token,
    list_api_keys,
    rotate_api_key,
)
from core.api_key_management.scopes import (
    required_scopes_for_route,
    is_allowed,
    resolve_api_key_scopes,
    get_valid_scopes,
    role_has_admin_bypass,
    playground_mcp_scopes,
    scope_enforcement_enabled,
    caller_has_scope,
)
from utils.server_config import OAUTH_VALID_SCOPES


@pytest.fixture(autouse=True)
def _enforcement_on(monkeypatch):
    monkeypatch.setattr(
        "core.scope_management.policy.scope_enforcement_enabled", lambda: True
    )
    monkeypatch.setattr(
        "core.scope_management.policy.get_enforcement_mode", lambda: "enforce"
    )


@pytest.fixture(autouse=True)
async def cleanup_api_keys():
    async with get_async_session() as session:
        await session.execute(delete(ApiKey))
        await session.commit()
    yield
    async with get_async_session() as session:
        await session.execute(delete(ApiKey))
        await session.commit()


def test_required_scopes_for_route():
    assert required_scopes_for_route("pluginA", ["tagX", "tagY"]) == {
        "plugin:pluginA", "group:pluginA:tagX", "group:pluginA:tagY",
    }
    assert required_scopes_for_route("pluginA", []) == {"plugin:pluginA"}
    assert required_scopes_for_route("", ["tagX"]) == set()


def test_is_allowed():
    required = {"plugin:a", "group:a:t"}
    assert is_allowed(None, required) is True
    assert is_allowed([], required) is False
    assert is_allowed(["plugin:a"], required) is True
    assert is_allowed(["group:a:t"], required) is True
    assert is_allowed(["group:a:other"], required) is False
    # Fail-closed: authenticated + empty required (unresolved plugin) → deny
    assert is_allowed(["anything"], set()) is False
    assert is_allowed(["all"], set()) is True  # all/* bypass still works
    assert is_allowed(["group:testplug:read"], {"group:testplug:read"}) is True


def test_is_allowed_when_scope_enforcement_disabled(monkeypatch):
    import core.api_key_management.scopes as scopes_mod
    scopes_mod._scope_off_logged = False
    monkeypatch.setattr(scopes_mod, "scope_enforcement_enabled", lambda: False)
    monkeypatch.setattr(
        "core.scope_management.policy.scope_enforcement_enabled", lambda: False
    )
    required = {"plugin:jules_plugin", "group:jules_plugin:sessions"}
    assert is_allowed([], required) is True
    assert caller_has_scope([], "terminal:use") is True


def test_is_allowed_admin_bypass():
    required = {"plugin:jules_plugin", "group:jules_plugin:sessions"}
    # `admin` master scope is a full bypass regardless of required tokens.
    assert is_allowed(["admin"], required) is True
    assert is_allowed(["whiskers", "admin"], required) is True
    # Non-admin caller without the specific token is still denied.
    assert is_allowed(["whiskers"], required) is False


def test_resolve_api_key_scopes(monkeypatch):
    assert resolve_api_key_scopes({"scopes": None}) == []
    assert resolve_api_key_scopes({}) == []
    assert resolve_api_key_scopes({"scopes": []}) == []
    assert resolve_api_key_scopes({"scopes": ["plugin:foo"]}) == ["plugin:foo"]

    import utils.server_config
    monkeypatch.setattr(utils.server_config, "API_KEY_NULL_SCOPES_POLICY", "legacy_full")
    assert resolve_api_key_scopes({"scopes": None}) == get_valid_scopes()
    assert resolve_api_key_scopes({}) == get_valid_scopes()
    assert resolve_api_key_scopes({"scopes": []}) == []
    assert resolve_api_key_scopes({"scopes": ["plugin:foo"]}) == ["plugin:foo"]


@pytest.mark.asyncio
async def test_create_and_lookup_with_scopes():
    created = await create_api_key("test_sub", "scoped-key", scopes=["plugin:foo"])
    assert created["scopes"] == ["plugin:foo"]

    looked_up = await lookup_active_by_token(created["token"])
    assert looked_up["scopes"] == ["plugin:foo"]


@pytest.mark.asyncio
async def test_create_without_scopes_defaults_all_sentinel():
    """Omitting scopes (None) normalizes to ['all'] (UI full-access sentinel)."""
    created = await create_api_key("test_sub", "legacy-key")
    assert created["scopes"] == ["all"]

    looked_up = await lookup_active_by_token(created["token"])
    assert looked_up["scopes"] == ["all"]


@pytest.mark.asyncio
async def test_create_with_empty_scopes_deny_all():
    created = await create_api_key("test_sub", "deny-key", scopes=[])
    assert created["scopes"] == []


@pytest.mark.asyncio
async def test_list_api_keys_includes_scopes():
    await create_api_key("test_sub", "k1", scopes=["plugin:a"], tenant_id=1)
    keys = await list_api_keys("test_sub", tenant_id=1)
    assert len(keys) == 1
    assert keys[0]["scopes"] == ["plugin:a"]


@pytest.mark.asyncio
async def test_rotate_carries_scopes_forward():
    orig = await create_api_key("test_sub", "rot-key", scopes=["plugin:bar"], tenant_id=1)
    replacement = await rotate_api_key("test_sub", orig["key_id"], tenant_id=1)
    assert replacement["scopes"] == ["plugin:bar"]


def test_data_only_key_denied_for_terminal():
    data_scopes = ["plugin:fake_plugin", "group:fake_plugin:read"]
    assert is_allowed(data_scopes, {"terminal:use"}) is False
    assert is_allowed(data_scopes, required_scopes_for_route("cat_terminal_relay_plugin", ["terminal"])) is False


def test_scopes_for_role_master_includes_contributed_token():
    from core.api_key_management.scopes import scopes_for_role
    from core.plugin_loader.scope_registry import set_plugin_scopes, clear

    set_plugin_scopes("testplug", ["plugin:testplug"])
    try:
        master_scopes = scopes_for_role("master")
        assert "plugin:testplug" in master_scopes
    finally:
        clear()


def test_role_has_admin_bypass_defaults(monkeypatch):
    import utils.server_config
    monkeypatch.setattr(utils.server_config, "ADMIN_BYPASS_ROLES", ["master", "admin"])
    assert role_has_admin_bypass("master") is True
    assert role_has_admin_bypass("admin") is True
    assert role_has_admin_bypass("viewer") is False
    assert role_has_admin_bypass(None) is False


def test_playground_mcp_scopes_admin_bypass(monkeypatch):
    import utils.server_config
    monkeypatch.setattr(utils.server_config, "ADMIN_BYPASS_ROLES", ["master", "admin"])
    monkeypatch.setattr(utils.server_config, "OAUTH_VALID_SCOPES", ["whiskers"])
    scopes = playground_mcp_scopes("admin")
    assert "admin" in scopes
    assert "whiskers" in scopes


def test_playground_mcp_scopes_falsy_role_returns_empty(monkeypatch):
    import utils.server_config
    monkeypatch.setattr(utils.server_config, "OAUTH_VALID_SCOPES", ["whiskers", "terminal:use"])
    assert playground_mcp_scopes(None) == []
    assert playground_mcp_scopes("") == []


def test_playground_mcp_scopes_non_bypass_role(monkeypatch):
    import utils.server_config
    monkeypatch.setattr(utils.server_config, "ADMIN_BYPASS_ROLES", ["master", "admin"])
    monkeypatch.setattr(utils.server_config, "ROLES_CONFIG", {
        "viewer": {"scopes": ["whiskers"]},
    })
    assert playground_mcp_scopes("viewer") == ["whiskers"]


def test_playground_mcp_scopes_unresolved_role_denies_all():
    """Missing ocat_role must not escalate to full get_valid_scopes()."""
    assert playground_mcp_scopes(None) == []


def test_proxy_scope_tokens_register_and_clear():
    from core.plugin_loader.scope_registry import set_plugin_scopes, clear, get_plugin_scopes

    pid = "proxy_Notion-AndrewDev"
    set_plugin_scopes(pid, [
        {"token": f"plugin:{pid}", "description": "All tools"},
        {"token": f"group:{pid}:proxy", "description": "Proxy group"},
    ])
    try:
        entries = get_plugin_scopes(pid)
        tokens = {e["token"] for e in entries}
        assert tokens == {f"plugin:{pid}", f"group:{pid}:proxy"}
        assert f"plugin:{pid}" in get_valid_scopes()
        assert f"group:{pid}:proxy" in get_valid_scopes()

        required = required_scopes_for_route(pid, (pid, "proxy"))
        assert required & tokens
    finally:
        clear(pid)
        assert get_plugin_scopes(pid) == []
        assert f"plugin:{pid}" not in get_valid_scopes()