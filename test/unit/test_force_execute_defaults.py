"""Unit tests for auth-based force_execute defaults (no DB)."""

from core.api_key_management.scopes import (
    coerce_force_execute,
    default_force_execute_authenticated,
    resolve_force_execute,
    role_defaults_force_execute,
)


def test_role_defaults_force_execute():
    """Any non-empty role defaults force_execute; missing role does not."""
    assert role_defaults_force_execute("master") is True
    assert role_defaults_force_execute("admin") is True
    assert role_defaults_force_execute("operator") is True
    assert role_defaults_force_execute("viewer") is True
    assert role_defaults_force_execute(None) is False
    assert role_defaults_force_execute("") is False


def test_default_force_execute_authenticated():
    """Authenticated principals default force_execute; scopes authorize tools."""
    assert default_force_execute_authenticated(True) is True
    assert default_force_execute_authenticated(False) is False


def test_coerce_force_execute():
    """Wire forms coerce to bool for force_execute."""
    assert coerce_force_execute(True) is True
    assert coerce_force_execute(False) is False
    assert coerce_force_execute("true") is True
    assert coerce_force_execute("FALSE") is False
    assert coerce_force_execute("1") is True
    assert coerce_force_execute(0) is False


def test_resolve_force_execute():
    """Explicit wins; authenticated kwarg preferred; any role falls back True."""
    assert resolve_force_execute({"force_execute": False}, "master") is False
    assert resolve_force_execute({"force_execute": "false"}, "admin", authenticated=True) is False

    assert resolve_force_execute({"force_execute": True}, "viewer") is True
    assert resolve_force_execute({"force_execute": "true"}, None, authenticated=False) is True

    assert resolve_force_execute({}, "viewer", authenticated=True) is True
    assert resolve_force_execute({}, None, authenticated=True) is True
    assert resolve_force_execute({}, "master", authenticated=False) is False

    assert resolve_force_execute({}, "master") is True
    assert resolve_force_execute({}, "viewer") is True
    assert resolve_force_execute({}, None) is False
