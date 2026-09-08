"""Unit tests for core.scope_management ScopeManager + is_allowed parity."""

import pytest

from core.scope_management import (
    PrincipalKind,
    ScopeGrant,
    evaluate_access,
    get_scope_manager,
    is_allowed,
    _set_scope_manager,
)
from core.scope_management.manager import _build_default_manager


@pytest.fixture(autouse=True)
def _fresh_manager(monkeypatch):
    """Reset singleton between tests; force enforcement on."""
    import core.scope_management.policy as policy_mod
    monkeypatch.setattr(policy_mod, "scope_enforcement_enabled", lambda: True)
    monkeypatch.setattr(policy_mod, "get_enforcement_mode", lambda: "enforce")
    _set_scope_manager(_build_default_manager())
    yield
    _set_scope_manager(None)


def test_is_allowed_none_scopes():
    required = {"plugin:a", "group:a:t"}
    assert is_allowed(None, required) is True


def test_is_allowed_admin_bypass():
    required = {"plugin:jules_plugin", "group:jules_plugin:sessions"}
    assert is_allowed(["admin"], required) is True
    assert is_allowed(["whiskers", "admin"], required) is True


def test_is_allowed_all_wildcard():
    required = {"plugin:jules_plugin"}
    assert is_allowed(["all"], required) is True
    assert is_allowed(["*"], required) is True


def test_is_allowed_intersection():
    required = {"plugin:a", "group:a:t"}
    assert is_allowed(["plugin:a"], required) is True
    assert is_allowed(["group:a:t"], required) is True
    assert is_allowed(["group:a:other"], required) is False
    assert is_allowed([], required) is False


def test_is_allowed_empty_required_authenticated_denies():
    """Fail-closed: authenticated + empty required → deny (C09)."""
    assert is_allowed(["anything"], set()) is False
    assert is_allowed([], set()) is False


def test_is_allowed_enforcement_off(monkeypatch):
    import core.scope_management.policy as policy_mod
    monkeypatch.setattr(policy_mod, "scope_enforcement_enabled", lambda: False)
    required = {"plugin:jules_plugin"}
    assert is_allowed([], required) is True


def test_evaluate_local_cli_unrestricted():
    grant = ScopeGrant(scopes=None, role=None, kind=PrincipalKind.LOCAL_CLI)
    d = evaluate_access(grant, plugin_id="p", tags=["t"], path="graph_step")
    assert d.allowed is True
    assert d.reason == "bypass_unrestricted"


def test_evaluate_anonymous_denies():
    grant = ScopeGrant(scopes=[], role=None, kind=PrincipalKind.ANONYMOUS)
    d = evaluate_access(grant, plugin_id="p", tags=["t"], path="direct_tool")
    assert d.allowed is False
    # deny_anonymous must win over tag_intersection so middleware can emit
    # "authentication required" rather than a generic scope deny.
    assert d.reason == "deny_anonymous"


def test_evaluate_anonymous_with_scopes_still_deny_anonymous():
    """ANONYMOUS with non-empty scopes that miss required still reasons as anon."""
    grant = ScopeGrant(scopes=["whiskers"], role=None, kind=PrincipalKind.ANONYMOUS)
    d = evaluate_access(grant, plugin_id="p", tags=["t"], path="direct_tool")
    assert d.allowed is False
    assert d.reason == "deny_anonymous"


def test_evaluate_plugin_token_allows():
    grant = ScopeGrant(scopes=["plugin:p"], role=None, kind=PrincipalKind.API_KEY)
    d = evaluate_access(grant, plugin_id="p", tags=["t"], path="direct_tool")
    assert d.allowed is True
    assert d.reason == "intersection"


def test_manager_register_rule_guard():
    mgr = get_scope_manager()
    with pytest.raises(TypeError):
        mgr.register_rule("not-a-rule")  # type: ignore[arg-type]
