"""Unit tests for core_graph.model_roles.registry — mirrors test_flow_registry_claims.py."""

from __future__ import annotations

import pytest

from core_graph.model_roles.registry import (
    _reset_model_roles_for_tests,
    get_model_role,
    get_model_role_registry,
    list_model_roles,
    register_model_role,
    unregister_model_role_owner,
)
from core_graph.model_roles.role_spec import parse_model_role_spec

PLUGIN_ROLE = {"role_id": "portfolio_layout", "ladder": [{"selector": "core"}]}


@pytest.fixture(autouse=True)
def _clean():
    _reset_model_roles_for_tests()
    yield
    _reset_model_roles_for_tests()


def test_core_defaults_auto_seed_on_first_access():
    roles = list_model_roles()
    ids = {r.role_id for r in roles}
    assert "triage" in ids
    assert "summary" in ids
    assert get_model_role_registry().source_of("triage") == "core"


def test_register_and_get_plugin_role():
    spec = parse_model_role_spec(PLUGIN_ROLE, owner="portfolio_plugin")
    register_model_role(spec)
    got = get_model_role("portfolio_layout")
    assert got is not None
    assert got.owner == "portfolio_plugin"
    assert get_model_role_registry().source_of("portfolio_layout") == "plugin"


def test_plugin_cannot_shadow_core_role_id():
    hijack = parse_model_role_spec(
        {"role_id": "triage", "ladder": [{"selector": "strongest"}]}, owner="evil_plugin"
    )
    register_model_role(hijack)
    effective = get_model_role("triage")
    assert effective.owner == "core"
    assert effective.ladder[0].selector == "core"


def test_unregister_owner_removes_only_its_roles():
    register_model_role(parse_model_role_spec(PLUGIN_ROLE, owner="portfolio_plugin"))
    register_model_role(
        parse_model_role_spec(
            {"role_id": "portfolio_bake", "ladder": [{"selector": "core"}]},
            owner="portfolio_plugin",
        )
    )
    register_model_role(
        parse_model_role_spec(
            {"role_id": "other_role", "ladder": [{"selector": "core"}]}, owner="other_plugin"
        )
    )
    assert unregister_model_role_owner("portfolio_plugin") == 2
    assert get_model_role("portfolio_layout") is None
    assert get_model_role("other_role") is not None


def test_db_override_beats_core_and_deleting_it_reverts():
    reg = get_model_role_registry()
    override = parse_model_role_spec(
        {"role_id": "triage", "ladder": [{"selector": "fast"}, {"selector": "strongest"}]},
        owner="db",
    )
    reg.set_db_override("triage", override)
    assert get_model_role("triage").ladder[0].selector == "fast"
    assert reg.source_of("triage") == "db"

    reg.set_db_override("triage", None)
    assert get_model_role("triage").ladder[0].selector == "core"
    assert reg.source_of("triage") == "core"


def test_db_override_beats_plugin_addition():
    reg = get_model_role_registry()
    register_model_role(parse_model_role_spec(PLUGIN_ROLE, owner="portfolio_plugin"))
    override = parse_model_role_spec(
        {"role_id": "portfolio_layout", "ladder": [{"selector": "strongest"}]}, owner="db"
    )
    reg.set_db_override("portfolio_layout", override)
    assert get_model_role("portfolio_layout").ladder[0].selector == "strongest"


def test_clear_resets_everything():
    register_model_role(parse_model_role_spec(PLUGIN_ROLE, owner="portfolio_plugin"))
    reg = get_model_role_registry()
    reg.set_db_override("triage", parse_model_role_spec(
        {"role_id": "triage", "ladder": [{"selector": "fast"}]}, owner="db"
    ))
    reg.clear()
    assert reg.get("portfolio_layout") is None
    assert reg.get("triage") is None  # core defaults dropped too — full reset


def test_unknown_role_id_returns_none():
    assert get_model_role("does_not_exist") is None
