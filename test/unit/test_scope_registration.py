"""Permission registration API — dynamic scope vocab front door.

Covers core.scope_management.registration (PermissionRegistry), the
ScopeManager delegates, the scope_registry back-compat shim, the
scopes.contribute plugin lifecycle event, and get_valid_scopes() inclusion.
"""

import pytest

from core.scope_management.registration import (
    PermissionRegistry,
    ScopePermission,
    get_permission_registry,
    register_plugin_permissions,
    unregister_plugin_permissions,
    validate_scope_token,
)


@pytest.fixture(autouse=True)
def _isolate():
    get_permission_registry().clear()
    yield
    get_permission_registry().clear()


def test_register_and_get():
    entries = register_plugin_permissions("plug_a", ["plugin:plug_a", "group:plug_a:read"])
    assert [e.token for e in entries] == ["plugin:plug_a", "group:plug_a:read"]
    got = get_permission_registry().get_plugin_permissions("plug_a")
    assert [e.token for e in got] == ["plugin:plug_a", "group:plug_a:read"]


def test_anti_squatting_rejected():
    register_plugin_permissions("plug_a", ["plugin:plug_a", "plugin:other", "group:other:x"])
    got = get_permission_registry().get_plugin_permissions("plug_a")
    assert [e.token for e in got] == ["plugin:plug_a"]


def test_replace_true_overwrites():
    register_plugin_permissions("plug_a", ["plugin:plug_a"], replace=True)
    register_plugin_permissions("plug_a", ["group:plug_a:write"], replace=True)
    got = get_permission_registry().get_plugin_permissions("plug_a")
    assert [e.token for e in got] == ["group:plug_a:write"]


def test_replace_false_merges_and_dedupes():
    register_plugin_permissions("plug_a", ["plugin:plug_a"], replace=True)
    register_plugin_permissions("plug_a", ["plugin:plug_a", "group:plug_a:write"], replace=False)
    got = get_permission_registry().get_plugin_permissions("plug_a")
    assert [e.token for e in got] == ["plugin:plug_a", "group:plug_a:write"]


def test_unregister_specific_and_all():
    register_plugin_permissions("a", ["plugin:a"])
    register_plugin_permissions("b", ["plugin:b"])
    unregister_plugin_permissions("a")
    assert get_permission_registry().get_plugin_permissions("a") == []
    assert [e.token for e in get_permission_registry().get_plugin_permissions("b")] == ["plugin:b"]
    unregister_plugin_permissions()
    assert get_permission_registry().get_plugin_permissions("b") == []


def test_synthetic_flag_set_query_and_clear():
    reg = get_permission_registry()
    assert reg.synthetic_plugin_ids() == []
    assert reg.is_synthetic("plug_a") is False

    reg.mark_synthetic("plug_a")
    assert reg.is_synthetic("plug_a") is True
    assert reg.synthetic_plugin_ids() == ["plug_a"]

    unregister_plugin_permissions("plug_a")
    assert reg.is_synthetic("plug_a") is False
    assert reg.synthetic_plugin_ids() == []


def test_synthetic_flag_cleared_on_full_clear():
    reg = get_permission_registry()
    reg.mark_synthetic("plug_a")
    reg.mark_synthetic("plug_b")
    unregister_plugin_permissions()
    assert reg.synthetic_plugin_ids() == []


def test_all_permissions_includes_plugin_id():
    register_plugin_permissions("a", [{"token": "plugin:a", "description": "A"}])
    # Reserved "core" namespace survives the autouse fixture's clear() (it is
    # not a plugin — see unregister_plugin_permissions' docstring); filter it
    # out here since this test only cares about plugin "a"'s own row.
    rows = [r for r in get_permission_registry().all_permissions() if r["plugin_id"] != "core"]
    assert rows == [{"token": "plugin:a", "description": "A", "plugin_id": "a"}]


def test_all_tokens_deduped():
    register_plugin_permissions("a", ["plugin:a", "group:a:read"])
    register_plugin_permissions("b", ["plugin:b", "group:a:read"])
    tokens = [t for t in get_permission_registry().all_tokens() if not t.startswith("core:")]
    assert tokens == ["plugin:a", "group:a:read", "plugin:b"]


def test_fingerprint_stable_and_changes():
    register_plugin_permissions("a", ["plugin:a"])
    fp1 = get_permission_registry().fingerprint("a")
    fp1_again = get_permission_registry().fingerprint("a")
    assert fp1 == fp1_again

    register_plugin_permissions("a", ["plugin:a", "group:a:read"], replace=True)
    fp2 = get_permission_registry().fingerprint("a")
    assert fp2 != fp1


def test_manager_delegates():
    from core.scope_management.manager import _build_default_manager

    mgr = _build_default_manager()
    mgr.register_plugin_permissions("plug_a", ["plugin:plug_a"])
    assert [e.token for e in mgr.permissions.get_plugin_permissions("plug_a")] == ["plugin:plug_a"]
    mgr.unregister_plugin_permissions("plug_a")
    assert mgr.permissions.get_plugin_permissions("plug_a") == []


def test_shim_delegates_to_registration():
    from core.plugin_loader import scope_registry

    scope_registry.set_plugin_scopes("plug_a", ["plugin:plug_a", "bogus"])
    assert scope_registry.get_plugin_scopes("plug_a") == [{"token": "plugin:plug_a", "description": ""}]
    tokens = [t for t in scope_registry.get_all_tokens() if not t.startswith("core:")]
    assert tokens == ["plugin:plug_a"]
    scope_registry.clear("plug_a")
    assert scope_registry.get_plugin_scopes("plug_a") == []


def test_get_valid_scopes_includes_registered_tokens():
    from core.scope_management.vocabulary import get_valid_scopes
    from utils.server_config import OAUTH_VALID_SCOPES

    register_plugin_permissions("plug_a", ["plugin:plug_a"])
    valid = get_valid_scopes()
    assert "plugin:plug_a" in valid
    assert set(OAUTH_VALID_SCOPES).issubset(set(valid))


def test_proxy_registration_via_front_door():
    from core.proxy.proxy_manager import _register_proxy_scopes, _clear_proxy_scopes

    _register_proxy_scopes("notion")
    tokens = get_permission_registry().all_tokens()
    assert "plugin:proxy_notion" in tokens
    assert "group:proxy_notion:proxy" in tokens
    _clear_proxy_scopes("notion")
    assert get_permission_registry().get_plugin_permissions("proxy_notion") == []


def test_scopes_contribute_event_merges_with_manifest():
    from core.plugin_loader.plugin_event_bus import PluginEventBus
    from core.plugin_loader.plugin_context import PluginContext

    class _FakeRegistry:
        def __init__(self):
            self.events = PluginEventBus()
            self.events.on(
                "scopes.contribute",
                lambda plugin_id, scopes: register_plugin_permissions(plugin_id, scopes, replace=False),
            )

    fake_registry = _FakeRegistry()
    register_plugin_permissions("plug_a", ["plugin:plug_a"], replace=True)
    ctx = PluginContext(
        app=None,
        events=fake_registry.events,
        config={},
        plugin_id="plug_a",
        registry=fake_registry,
    )
    ctx.contribute_scopes(["group:plug_a:runtime"])
    got = [e.token for e in get_permission_registry().get_plugin_permissions("plug_a")]
    assert got == ["plugin:plug_a", "group:plug_a:runtime"]


def test_full_clear_preserves_reserved_core_namespace():
    """Regression: core.plugin_loader.scope_registry.clear() (called with no
    args on every plugin-discovery boot pass) must never wipe the level-1
    core vocabulary — nothing else ever re-seeds it. Live-boot repro: the
    registry seeded 15 core entries on first construction, then the very
    next plugin-discovery pass's clear() zeroed it to 0 for the rest of the
    process, before scope-health even ran."""
    from core.scope_management.registration import seed_core_scopes

    reg = get_permission_registry()
    seed_core_scopes(reg)
    assert len(reg.get_plugin_permissions("core")) > 0

    register_plugin_permissions("some_plugin", ["plugin:some_plugin"])
    unregister_plugin_permissions()  # full clear, no plugin_id — the boot-pass call shape

    assert len(reg.get_plugin_permissions("core")) > 0
    assert reg.get_plugin_permissions("some_plugin") == []


def test_validate_scope_token_anti_squatting():
    assert validate_scope_token("plug_a", "plugin:plug_a") is True
    assert validate_scope_token("plug_a", "group:plug_a:read") is True
    assert validate_scope_token("plug_a", "plugin:other") is False
    assert validate_scope_token("plug_a", "group:other:read") is False
