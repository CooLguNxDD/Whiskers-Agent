"""scope_enforcement_mode: enforce | audit | off (double-key)."""

import pytest

from core.scope_management import (
    PrincipalKind,
    ScopeGrant,
    evaluate_access,
    _set_scope_manager,
)
from core.scope_management.manager import _build_default_manager
from core.scope_management.policy import get_enforcement_mode, MODE_ENFORCE, MODE_AUDIT, MODE_OFF


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    _set_scope_manager(_build_default_manager())
    yield
    _set_scope_manager(None)


def _deny_grant():
    return ScopeGrant(scopes=[], role=None, kind=PrincipalKind.API_KEY)


def test_enforce_denies(monkeypatch):
    monkeypatch.setattr(
        "core.scope_management.policy.get_enforcement_mode", lambda: MODE_ENFORCE
    )
    monkeypatch.setattr(
        "core.scope_management.policy.scope_enforcement_enabled", lambda: True
    )
    d = evaluate_access(_deny_grant(), plugin_id="p", tags=["t"])
    assert d.allowed is False


def test_audit_allows_with_reason(monkeypatch):
    monkeypatch.setattr(
        "core.scope_management.policy.get_enforcement_mode", lambda: MODE_AUDIT
    )
    monkeypatch.setattr(
        "core.scope_management.policy.scope_enforcement_enabled", lambda: True
    )
    d = evaluate_access(_deny_grant(), plugin_id="p", tags=["t"])
    assert d.allowed is True
    assert d.reason == "audit_allow"


def test_off_requires_double_key(monkeypatch):
    """Config mode=off alone still enforces without env SCOPE_ENFORCEMENT_OFF."""
    import utils.server_config as sc

    monkeypatch.setattr(sc, "SCOPE_ENFORCEMENT_MODE", "off")
    monkeypatch.delenv("SCOPE_ENFORCEMENT_OFF", raising=False)
    # Reset manager so policy re-reads
    _set_scope_manager(_build_default_manager())
    assert get_enforcement_mode() == MODE_ENFORCE

    monkeypatch.setenv("SCOPE_ENFORCEMENT_OFF", "1")
    assert get_enforcement_mode() == MODE_OFF

def test_raw_mode_invalid_logs_warning_once(monkeypatch, caplog):
    import utils.server_config as sc
    from core.scope_management.policy import _raw_mode, MODE_ENFORCE
    monkeypatch.setattr(sc, "SCOPE_ENFORCEMENT_MODE", "invalid_mode")
    import core.scope_management.policy as policy
    policy._mode_warn_logged = False
    assert _raw_mode() == MODE_ENFORCE
    assert "Invalid scope_enforcement_mode: 'invalid_mode'" in caplog.text
    caplog.clear()
    assert _raw_mode() == MODE_ENFORCE
    assert "Invalid scope_enforcement_mode: 'invalid_mode'" not in caplog.text
