"""Unit tests for core.scope_management.gates — parser, registry, ceiling rule."""

import pytest

from core.scope_management.gates import (
    GateSpecError,
    OperationGate,
    PluginGateRegistry,
    parse_gate_spec,
)
from core.scope_management import ScopeGrant, evaluate_access, _set_scope_manager
from core.scope_management.manager import _build_default_manager
from core.scope_management.principal import PrincipalKind
from core.scope_management.request import AccessRequest


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def test_parse_gate_spec_minimal():
    spec = parse_gate_spec("p", {})
    assert spec.plugin_id == "p"
    assert spec.core == ()
    assert spec.access == "read"
    assert spec.operations.allow == ("*",)
    assert spec.operations.deny == ()
    assert spec.endpoints == ()


def test_parse_gate_spec_bare_core_domain_uses_access():
    spec = parse_gate_spec("p", {"core": ["whiskers.proxy"], "access": "write"})
    assert spec.core == ("core:whiskers.proxy:write",)


def test_parse_gate_spec_full_core_token_kept_as_is():
    spec = parse_gate_spec("p", {"core": ["core:graph:read"]})
    assert spec.core == ("core:graph:read",)


def test_parse_gate_spec_rejects_unknown_top_level_key():
    with pytest.raises(GateSpecError):
        parse_gate_spec("p", {"bogus": 1})


def test_parse_gate_spec_rejects_bad_access():
    with pytest.raises(GateSpecError):
        parse_gate_spec("p", {"access": "delete"})


def test_parse_gate_spec_rejects_non_dict():
    with pytest.raises(GateSpecError):
        parse_gate_spec("p", "not-a-dict")  # type: ignore[arg-type]


def test_parse_gate_spec_requires_plugin_id():
    with pytest.raises(GateSpecError):
        parse_gate_spec("", {})


def test_parse_gate_spec_operations_unknown_key():
    with pytest.raises(GateSpecError):
        parse_gate_spec("p", {"operations": {"bogus": []}})


def test_operation_gate_deny_wins_over_allow():
    gate = OperationGate(allow=("*",), deny=("delete_*",))
    assert gate.permits("create_thing") is True
    assert gate.permits("delete_thing") is False


def test_plugin_gate_permits_endpoint_empty_means_unrestricted():
    spec = parse_gate_spec("p", {})
    assert spec.permits_endpoint("/anything") is True


def test_plugin_gate_permits_endpoint_glob():
    spec = parse_gate_spec("p", {"endpoints": ["/api/portfolio/public/*"]})
    assert spec.permits_endpoint("/api/portfolio/public/layout/123") is True
    assert spec.permits_endpoint("/api/portfolio/admin/anything") is False


def test_plugin_gate_permits_core_empty_means_no_reach():
    spec = parse_gate_spec("p", {})
    assert spec.permits_core("core:graph:read") is False


# ---------------------------------------------------------------------------
# registry: manifest vs DB override precedence
# ---------------------------------------------------------------------------


def test_registry_no_gate_returns_none():
    reg = PluginGateRegistry()
    assert reg.get_gate("p") is None


def test_registry_manifest_gate_used_when_no_override():
    reg = PluginGateRegistry()
    spec = parse_gate_spec("p", {"core": ["core:graph:read"]})
    reg.register_manifest_gate(spec)
    assert reg.get_gate("p") is spec


def test_registry_db_override_fully_replaces_manifest():
    reg = PluginGateRegistry()
    manifest_spec = parse_gate_spec("p", {"core": ["core:graph:read"]})
    db_spec = parse_gate_spec("p", {"core": ["core:config:write"]}, owner="db")
    reg.register_manifest_gate(manifest_spec)
    reg.set_db_override("p", db_spec)
    effective = reg.get_gate("p")
    assert effective is db_spec
    assert effective.core == ("core:config:write",)


def test_registry_clear_db_overrides_reverts_to_manifest():
    reg = PluginGateRegistry()
    manifest_spec = parse_gate_spec("p", {"core": ["core:graph:read"]})
    db_spec = parse_gate_spec("p", {"core": []}, owner="db")
    reg.register_manifest_gate(manifest_spec)
    reg.set_db_override("p", db_spec)
    reg.clear_db_overrides()
    assert reg.get_gate("p") is manifest_spec


def test_registry_all_gated_plugin_ids():
    reg = PluginGateRegistry()
    reg.register_manifest_gate(parse_gate_spec("a", {}))
    reg.set_db_override("b", parse_gate_spec("b", {}, owner="db"))
    assert reg.all_gated_plugin_ids() == ["a", "b"]


# ---------------------------------------------------------------------------
# rule-chain integration: ceiling rule ordering, admin bypass
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_manager_and_gate_registry(monkeypatch):
    import core.scope_management.policy as policy_mod
    from core.scope_management.gates import _set_plugin_gate_registry

    monkeypatch.setattr(policy_mod, "scope_enforcement_enabled", lambda: True)
    monkeypatch.setattr(policy_mod, "get_enforcement_mode", lambda: "enforce")
    _set_scope_manager(_build_default_manager())
    _set_plugin_gate_registry(PluginGateRegistry())
    yield
    _set_scope_manager(None)
    _set_plugin_gate_registry(None)


def test_absent_gate_is_unconstrained():
    """No gate registered for the plugin -> ceiling rule is a no-op."""
    grant = ScopeGrant(scopes=["plugin:p"], role=None, kind=PrincipalKind.API_KEY)
    request = AccessRequest(plugin_id="p", operation_id="anything")
    decision = evaluate_access(grant, request=request, required={"plugin:p"})
    assert decision.allowed is True
    assert decision.reason == "intersection"


def test_gate_denies_operation_outside_allowlist():
    from core.scope_management.gates import get_plugin_gate_registry

    get_plugin_gate_registry().register_manifest_gate(
        parse_gate_spec("p", {"operations": {"allow": ["safe_op"]}})
    )
    grant = ScopeGrant(scopes=["plugin:p"], role=None, kind=PrincipalKind.API_KEY)
    request = AccessRequest(plugin_id="p", operation_id="other_op")
    decision = evaluate_access(grant, request=request, required={"plugin:p"})
    assert decision.allowed is False
    assert decision.reason == "deny_gate_operation"


def test_gate_admin_bypasses_ceiling():
    """Admin/all bypass runs before the gate rule — the ceiling never applies to admin."""
    from core.scope_management.gates import get_plugin_gate_registry

    get_plugin_gate_registry().register_manifest_gate(
        parse_gate_spec("p", {"operations": {"allow": ["safe_op"]}})
    )
    grant = ScopeGrant(scopes=["admin"], role="master", kind=PrincipalKind.API_KEY)
    request = AccessRequest(plugin_id="p", operation_id="other_op")
    decision = evaluate_access(grant, request=request, required={"plugin:p"})
    assert decision.allowed is True
    assert decision.reason == "bypass_admin"


def test_gate_permits_core_matches_manifest_domain():
    """The terminal relay's gate.core must include the sandbox subdomain
    token, since permits_core matches exactly (via expand_implied closures)
    and core:terminal:write does not widen into core:terminal.sandbox:write
    (grammar.expand_implied is same-domain only)."""
    spec = parse_gate_spec(
        "cat_terminal_relay_plugin",
        {"core": ["core:terminal:read", "core:terminal:write"]},
    )
    assert spec.permits_core("core:terminal.sandbox:write") is False

    spec_with_sandbox = parse_gate_spec(
        "cat_terminal_relay_plugin",
        {"core": ["core:terminal:read", "core:terminal:write", "core:terminal.sandbox:write"]},
    )
    assert spec_with_sandbox.permits_core("core:terminal.sandbox:write") is True


def test_catalog_execute_authorize_enforces_gate(monkeypatch):
    """Regression for the catalog-execute gate bypass: _authorize must build
    a real AccessRequest so _plugin_gate_ceiling actually runs, instead of
    going through the compat is_allowed(scopes, required) shortcut which
    passes request=None and no-ops the ceiling rule."""
    from dataclasses import dataclass

    from core.scope_management.gates import get_plugin_gate_registry
    from core.route_registry.execute import _authorize, ExecuteError
    from core.route_registry.operation_descriptor import AccessClass

    get_plugin_gate_registry().register_manifest_gate(
        parse_gate_spec("p", {"operations": {"deny": ["denied_op"]}})
    )

    @dataclass
    class _FakeOp:
        plugin_id: str = "p"
        operation_id: str = "denied_op"
        tags: tuple = ()
        access: AccessClass = AccessClass.READ
        required_scopes: tuple = ()

    op = _FakeOp()
    with pytest.raises(ExecuteError) as excinfo:
        _authorize(op, caller_scopes=["plugin:p"])
    assert excinfo.value.status == 403
    assert excinfo.value.details["reason"] == "deny_gate_operation"

    # A sibling, non-denied operation under the same gate still executes.
    op_ok = _FakeOp(operation_id="other_op")
    _authorize(op_ok, caller_scopes=["plugin:p"])  # must not raise

    # admin/all still bypass the gate entirely.
    _authorize(op, caller_scopes=["all"])  # must not raise
