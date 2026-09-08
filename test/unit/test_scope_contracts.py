"""Contract matrix C01–C20 for scope enforcement access paths."""

import pytest

from core.scope_management import ScopeGrant, evaluate_access, _set_scope_manager
from core.scope_management.contracts import ACCESS_PATH_CONTRACTS, GATE_AND_GRAMMAR_CONTRACTS
from core.scope_management.manager import _build_default_manager
from core.scope_management.registration import (
    get_permission_registry,
    register_plugin_permissions,
)
from core.scope_management.request import AccessRequest

# Plugin ids referenced by the contract matrix — registered dynamically here
# to exercise the registration API instead of relying on the grammar alone.
_CONTRACT_PLUGIN_IDS = (
    "fake_plugin",
    "jules_plugin",
    "proxy_notion",
    "cat_terminal_relay_plugin",
)


@pytest.fixture(autouse=True)
def _fresh_manager(monkeypatch):
    """Ensure enforcement is on, manager is fresh, and contract vocab is registered."""
    import core.scope_management.policy as policy_mod
    monkeypatch.setattr(policy_mod, "scope_enforcement_enabled", lambda: True)
    monkeypatch.setattr(policy_mod, "get_enforcement_mode", lambda: "enforce")
    _set_scope_manager(_build_default_manager())
    for plugin_id in _CONTRACT_PLUGIN_IDS:
        register_plugin_permissions(plugin_id, [f"plugin:{plugin_id}"], replace=True)
    yield
    _set_scope_manager(None)
    for plugin_id in _CONTRACT_PLUGIN_IDS:
        get_permission_registry().unregister_plugin_permissions(plugin_id)


@pytest.mark.parametrize(
    "row",
    ACCESS_PATH_CONTRACTS,
    ids=[c["id"] for c in ACCESS_PATH_CONTRACTS],
)
def test_access_path_contract(row):
    """Evaluate each golden contract row against evaluate_access."""
    p = row["principal"]
    a = row["action"]
    grant = ScopeGrant(
        scopes=p["scopes"],
        role=p.get("role"),
        kind=p["kind"],
    )
    decision = evaluate_access(
        grant,
        plugin_id=a.get("plugin_id") or "",
        tags=a.get("tags"),
        tool_name=a.get("tool_name") or "",
        path=a.get("path") or "",
    )
    want_allow = row["expected"] == "allow"
    assert decision.allowed is want_allow, (
        f"{row['id']} {row['description']}: "
        f"expected allowed={want_allow}, got allowed={decision.allowed} "
        f"reason={decision.reason} required={sorted(decision.required)}"
    )


@pytest.mark.parametrize(
    "row",
    GATE_AND_GRAMMAR_CONTRACTS,
    ids=[c["id"] for c in GATE_AND_GRAMMAR_CONTRACTS],
)
def test_gate_and_grammar_contract(row):
    """C13-C20: level-1 grammar closure/hierarchy + level-3 plugin-gate ceiling."""
    from core.scope_management.gates import get_plugin_gate_registry, parse_gate_spec

    registry = get_plugin_gate_registry()
    gate_cfg = row.get("gate")
    plugin_id = gate_cfg["plugin_id"] if gate_cfg else None
    if gate_cfg:
        registry.register_manifest_gate(parse_gate_spec(plugin_id, gate_cfg["spec"]))
        if "db_override_spec" in gate_cfg:
            registry.set_db_override(plugin_id, parse_gate_spec(plugin_id, gate_cfg["db_override_spec"], owner="db"))

    try:
        p = row["principal"]
        a = row["action"]
        grant = ScopeGrant(scopes=p["scopes"], role=p.get("role"), kind=p["kind"])
        request = AccessRequest(
            plugin_id=a.get("plugin_id") or "",
            tags=tuple(a.get("tags") or ()),
            **(row.get("request_extra") or {}),
        )
        decision = evaluate_access(grant, request=request, required=row.get("required"))
        want_allow = row["expected"] == "allow"
        assert decision.allowed is want_allow, (
            f"{row['id']} {row['description']}: "
            f"expected allowed={want_allow}, got allowed={decision.allowed} "
            f"reason={decision.reason} required={sorted(decision.required)}"
        )
    finally:
        if gate_cfg:
            registry.unregister_manifest_gate(plugin_id)
            registry.set_db_override(plugin_id, None)
