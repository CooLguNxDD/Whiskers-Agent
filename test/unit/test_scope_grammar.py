"""Unit tests for core.scope_management.grammar — parser + implied closure."""

import pytest

from core.scope_management.grammar import (
    ScopeKind,
    core_domain_covers,
    expand_implied,
    grant_covers_core,
    is_valid_scope_token,
    parse_scope,
    plugin_grant_covers,
)


@pytest.mark.parametrize(
    "token,kind",
    [
        ("all", ScopeKind.SENTINEL),
        ("*", ScopeKind.SENTINEL),
        ("admin", ScopeKind.SENTINEL),
        ("core:whiskers.proxy:read", ScopeKind.CORE),
        ("core:graph:write", ScopeKind.CORE),
        ("plugin:jules_plugin", ScopeKind.PLUGIN),
        ("plugin:jules_plugin:read", ScopeKind.PLUGIN),
        ("plugin:jules_plugin:write", ScopeKind.PLUGIN),
        ("group:jules_plugin:sessions", ScopeKind.GROUP),
        ("op:jules_plugin:create_session", ScopeKind.OP),
        ("", ScopeKind.INVALID),
        ("core:whiskers.proxy:delete", ScopeKind.INVALID),
        ("plugin:jules_plugin:delete", ScopeKind.INVALID),
        ("bogus", ScopeKind.INVALID),
    ],
)
def test_parse_scope_kind(token, kind):
    assert parse_scope(token).kind == kind


def test_is_valid_scope_token():
    assert is_valid_scope_token("core:graph:read") is True
    assert is_valid_scope_token("not a scope") is False


def test_expand_implied_core_write_implies_read():
    out = expand_implied("core:whiskers.proxy:write")
    assert out == {"core:whiskers.proxy:write", "core:whiskers.proxy:read"}


def test_expand_implied_core_read_is_terminal():
    assert expand_implied("core:whiskers.proxy:read") == {"core:whiskers.proxy:read"}


def test_expand_implied_bare_plugin_implies_write_and_read():
    out = expand_implied("plugin:jules_plugin")
    assert out == {
        "plugin:jules_plugin",
        "plugin:jules_plugin:write",
        "plugin:jules_plugin:read",
    }


def test_expand_implied_plugin_write_implies_read():
    out = expand_implied("plugin:jules_plugin:write")
    assert out == {"plugin:jules_plugin:write", "plugin:jules_plugin:read"}


def test_expand_implied_plugin_read_is_terminal():
    assert expand_implied("plugin:jules_plugin:read") == {"plugin:jules_plugin:read"}


def test_expand_implied_sentinel_and_invalid_expand_to_self():
    assert expand_implied("admin") == {"admin"}
    assert expand_implied("not a scope") == {"not a scope"}


def test_core_domain_covers_hierarchy():
    assert core_domain_covers("whiskers", "whiskers.proxy") is True
    assert core_domain_covers("whiskers.proxy", "whiskers.proxy") is True
    assert core_domain_covers("whiskers.proxy", "whiskers") is False
    assert core_domain_covers("whiskers.proxy", "whiskers.console") is False


def test_grant_covers_core_write_covers_subdomain_read():
    assert grant_covers_core("core:whiskers:write", "whiskers.proxy", "read") is True
    assert grant_covers_core("core:whiskers:read", "whiskers.proxy", "write") is False
    assert grant_covers_core("core:whiskers.proxy:read", "whiskers", "read") is False
    assert grant_covers_core("plugin:jules_plugin", "whiskers", "read") is False


def test_plugin_grant_covers_bare_covers_group_and_op():
    assert plugin_grant_covers("plugin:p", "p", group_tag="t") is True
    assert plugin_grant_covers("plugin:p", "p", op_id="foo") is True
    assert plugin_grant_covers("plugin:p:read", "p", group_tag="t") is True
    assert plugin_grant_covers("group:p:t", "p", group_tag="t") is True
    assert plugin_grant_covers("group:p:t", "p", group_tag="other") is False
    assert plugin_grant_covers("op:p:foo", "p", op_id="foo") is True
    assert plugin_grant_covers("op:p:foo", "p", op_id="bar") is False
    assert plugin_grant_covers("plugin:other", "p", group_tag="t") is False


def test_anti_squat_regexes_reject_cross_id():
    """Grammar parse alone doesn't enforce ownership — registration.py does —
    but a token declaring a different id than expected must still parse to
    that id, not silently match."""
    parsed = parse_scope("group:jules_plugin:sessions")
    assert parsed.id_or_domain == "jules_plugin"
    assert parsed.tag_or_op == "sessions"
