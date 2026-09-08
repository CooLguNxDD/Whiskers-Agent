"""
MTU-4a acceptance tests — plugin-declared scope registry.

Ground truth for the process-level scope store (sibling of skill_registry).
Scopes are declared in a plugin manifest ("scopes") and loaded at plugin load.
"""

import pytest

from core.plugin_loader.scope_registry import (
    normalize_scope_entries,
    validate_scope_token,
    set_plugin_scopes,
    get_plugin_scopes,
    get_all_scopes,
    get_all_tokens,
    clear,
)


@pytest.fixture(autouse=True)
def _isolate():
    clear()
    yield
    clear()


def test_normalize_object_and_shorthand_forms():
    raw = [
        {"token": "plugin:jules_plugin", "description": "Full access"},
        "group:jules_plugin:read",
    ]
    entries = normalize_scope_entries("jules_plugin", raw)
    assert entries == [
        {"token": "plugin:jules_plugin", "description": "Full access"},
        {"token": "group:jules_plugin:read", "description": ""},
    ]


def test_invalid_tokens_skipped_not_registered():
    set_plugin_scopes(
        "jules_plugin",
        [
            {"token": "plugin:jules_plugin", "description": "ok"},
            "bogus",
            "group:other_plugin:x",
            "plugin:not_me",
            "terminal:use",
        ],
    )
    got = get_plugin_scopes("jules_plugin")
    assert got == [{"token": "plugin:jules_plugin", "description": "ok"}]


def test_validate_scope_token_anti_squatting():
    assert validate_scope_token("jules_plugin", "plugin:jules_plugin") is True
    assert validate_scope_token("jules_plugin", "group:jules_plugin:read") is True
    assert validate_scope_token("jules_plugin", "plugin:other") is False
    assert validate_scope_token("jules_plugin", "group:other:read") is False
    assert validate_scope_token("jules_plugin", "terminal:use") is False
    assert validate_scope_token("jules_plugin", "bogus") is False
    assert validate_scope_token("jules_plugin", "group:jules_plugin:") is False
    assert validate_scope_token("jules_plugin", "group:jules_plugin:has space") is False


def test_clear_named_only():
    set_plugin_scopes("a", ["plugin:a"])
    set_plugin_scopes("b", ["plugin:b"])
    clear("a")
    assert get_plugin_scopes("a") == []
    assert get_plugin_scopes("b") == [{"token": "plugin:b", "description": ""}]


def test_clear_all():
    set_plugin_scopes("a", ["plugin:a"])
    set_plugin_scopes("b", ["plugin:b"])
    clear()
    assert get_plugin_scopes("a") == []
    assert get_plugin_scopes("b") == []
    # The reserved "core" namespace survives a full clear by design (it is
    # not a plugin) — see PermissionRegistry.unregister_plugin_permissions.
    assert [r for r in get_all_scopes() if r["plugin_id"] != "core"] == []


def test_hot_swap_reseed():
    set_plugin_scopes("p", ["plugin:p"])
    clear("p")
    assert get_plugin_scopes("p") == []
    set_plugin_scopes("p", [{"token": "group:p:read", "description": "read"}])
    assert get_plugin_scopes("p") == [{"token": "group:p:read", "description": "read"}]


def test_get_all_scopes_includes_plugin_id():
    set_plugin_scopes("a", [{"token": "plugin:a", "description": "A"}])
    set_plugin_scopes("b", [{"token": "group:b:read", "description": "B"}])
    rows = get_all_scopes()
    assert {r["plugin_id"] for r in rows if r["plugin_id"] != "core"} == {"a", "b"}
    assert all("token" in r and "description" in r for r in rows)


def test_get_all_tokens_deduped_across_plugins():
    set_plugin_scopes("a", ["plugin:a", "group:a:read"])
    set_plugin_scopes("b", ["plugin:b", "group:a:read"])
    tokens = [t for t in get_all_tokens() if not t.startswith("core:")]
    assert tokens == ["plugin:a", "group:a:read", "plugin:b"]


def test_get_valid_scopes_union_deduped_sorted():
    from core.api_key_management.scopes import get_valid_scopes
    from utils.server_config import OAUTH_VALID_SCOPES

    # The reserved "core" namespace is always present (seeded once, survives
    # clear()) — filter core:* tokens out to isolate this test's own concern
    # (the OAUTH_VALID_SCOPES ∪ plugin-registry union), not level-1 vocab.
    def _no_core(scopes):
        return sorted(s for s in scopes if not s.startswith("core:"))

    base_scopes = _no_core(get_valid_scopes())
    assert set(OAUTH_VALID_SCOPES).issubset(set(base_scopes))
    assert base_scopes == sorted(list(set(OAUTH_VALID_SCOPES)))

    set_plugin_scopes("my_plugin", ["plugin:my_plugin", "group:my_plugin:write"])

    extended_scopes = _no_core(get_valid_scopes())
    assert "plugin:my_plugin" in extended_scopes
    assert "group:my_plugin:write" in extended_scopes
    assert extended_scopes == sorted(set(OAUTH_VALID_SCOPES) | {"plugin:my_plugin", "group:my_plugin:write"})

    clear("my_plugin")
    cleared_scopes = _no_core(get_valid_scopes())
    assert "plugin:my_plugin" not in cleared_scopes
    assert "group:my_plugin:write" not in cleared_scopes
    assert cleared_scopes == sorted(list(set(OAUTH_VALID_SCOPES)))