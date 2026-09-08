"""Unit tests for core_graph.model_roles.role_spec.parse_model_role_spec."""

from __future__ import annotations

import pytest

from core_graph.model_roles.role_spec import (
    ModelRoleSpecError,
    parse_model_role_bundle,
    parse_model_role_spec,
)

MINIMAL = {
    "role_id": "triage",
    "ladder": [{"selector": "core"}],
}


def test_parse_minimal_ok():
    spec = parse_model_role_spec(MINIMAL, owner="plugin_x")
    assert spec.role_id == "triage"
    assert spec.owner == "plugin_x"
    assert len(spec.ladder) == 1
    assert spec.ladder[0].selector == "core"
    assert spec.terminal_fallback == "ctx_llm"


def test_unknown_top_level_key_rejected():
    bad = {**MINIMAL, "bogus": 1}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_unknown_rung_key_rejected():
    bad = {**MINIMAL, "ladder": [{"selector": "core", "bogus": 1}]}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_unknown_validation_key_rejected():
    bad = {**MINIMAL, "validate": {"bogus": 1}}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_unknown_condition_key_rejected():
    bad = {**MINIMAL, "entry_conditions": [{"field": "x", "op": "truthy", "bogus": 1}]}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_missing_role_id_rejected():
    bad = {k: v for k, v in MINIMAL.items() if k != "role_id"}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_empty_ladder_rejected():
    bad = {**MINIMAL, "ladder": []}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_empty_selector_rejected():
    bad = {**MINIMAL, "ladder": [{"selector": ""}]}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_max_attempts_lt_1_rejected():
    bad = {**MINIMAL, "ladder": [{"selector": "core", "max_attempts": 0}]}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_bad_condition_op_rejected():
    bad = {**MINIMAL, "entry_conditions": [{"field": "x", "op": "bogus"}]}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_bad_terminal_fallback_rejected():
    bad = {**MINIMAL, "terminal_fallback": "explode"}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_enum_values_without_enum_field_rejected():
    bad = {**MINIMAL, "validate": {"enum_values": ["a", "b"]}}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_spec(bad)


def test_unknown_pool_name_selector_is_accepted():
    """Documented exception: name-selector existence is validated later by
    resolver.compile_selector, not by the parser (pool is dynamic)."""
    ok = {**MINIMAL, "ladder": [{"selector": "totally-made-up-pool-name"}]}
    spec = parse_model_role_spec(ok)
    assert spec.ladder[0].selector == "totally-made-up-pool-name"


def test_multi_rung_ladder_with_validation_and_conditions():
    ok = {
        "role_id": "triage",
        "ladder": [{"selector": "fast"}, {"selector": "strongest", "max_attempts": 2}],
        "validate": {
            "require_json": True,
            "enum_field": "mode",
            "enum_values": ["chat", "classic", "specialist"],
        },
        "entry_conditions": [{"field": "repeat_failure_count", "op": "gt", "value": 0}],
        "terminal_fallback": "none",
    }
    spec = parse_model_role_spec(ok)
    assert len(spec.ladder) == 2
    assert spec.ladder[1].max_attempts == 2
    assert spec.validate.enum_values == ("chat", "classic", "specialist")
    assert spec.entry_conditions[0].op == "gt"
    assert spec.terminal_fallback == "none"


def test_bundle_parses_multiple_roles():
    bundle = {"roles": [MINIMAL, {"role_id": "chat", "ladder": [{"selector": "core"}]}]}
    specs = parse_model_role_bundle(bundle, owner="core")
    assert {s.role_id for s in specs} == {"triage", "chat"}
    assert all(s.owner == "core" for s in specs)


def test_bundle_duplicate_role_id_rejected():
    bundle = {"roles": [MINIMAL, MINIMAL]}
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_bundle(bundle)


def test_bundle_requires_roles_list():
    with pytest.raises(ModelRoleSpecError):
        parse_model_role_bundle({"roles": "nope"})
