"""Unit test for core.scope_management.health.run_boot_scope_health."""

import pytest

from core.scope_management.health import run_boot_scope_health


@pytest.fixture(autouse=True)
def _isolate():
    from core.scope_management.registration import get_permission_registry

    get_permission_registry().clear()
    yield
    get_permission_registry().clear()


def test_run_boot_scope_health_clean_at_boot():
    """No warnings against the real default rule chain + seeded vocab/registry.

    ``PermissionRegistry.clear()`` (full wipe) preserves the reserved "core"
    namespace by design (it is not a plugin), but a test double swapped in
    via ``_set_permission_registry`` could still start with no core entries
    at all — reseed explicitly so this check never depends on suite-order
    luck.
    """
    from core.scope_management.registration import get_permission_registry, seed_core_scopes

    seed_core_scopes(get_permission_registry())

    warnings = run_boot_scope_health()
    assert warnings == []


def test_run_boot_scope_health_flags_synthetic_plugin():
    """A plugin running on the fallback scope floor surfaces a warning
    instead of booting silently admin-only."""
    from core.scope_management.registration import get_permission_registry, seed_core_scopes

    reg = get_permission_registry()
    seed_core_scopes(reg)
    reg.mark_synthetic("no_scopes_plugin")

    warnings = run_boot_scope_health()
    assert any("scopes_synthetic" in w and "no_scopes_plugin" in w for w in warnings)
