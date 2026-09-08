"""Unit tests for core_graph.subgraphs.specialist.flow_spec.parse_flow_spec."""

from __future__ import annotations

import pytest

from core_graph.subgraphs.specialist.flow_spec import (
    FlowSpecError,
    parse_flow_spec,
)

MINIMAL = {
    "flow_id": "f1",
    "name": "Flow One",
    "stages": [{"id": "s1", "kind": "deterministic", "op": "plugin/op"}],
}


def test_parse_minimal_ok():
    spec = parse_flow_spec(MINIMAL, owner="plugin_x")
    assert spec.flow_id == "f1"
    assert spec.owner == "plugin_x"
    assert len(spec.stages) == 1
    assert spec.stages[0].kind == "deterministic"
    assert spec.stages[0].on_fail == "fail_closed"


def test_unknown_top_level_key_rejected():
    bad = {**MINIMAL, "bogus": 1}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_unknown_stage_key_rejected():
    bad = {**MINIMAL, "stages": [{**MINIMAL["stages"][0], "bogus": 1}]}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_missing_flow_id_rejected():
    bad = {k: v for k, v in MINIMAL.items() if k != "flow_id"}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_empty_stages_rejected():
    bad = {**MINIMAL, "stages": []}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_duplicate_stage_ids_rejected():
    bad = {
        **MINIMAL,
        "stages": [
            {"id": "s1", "kind": "deterministic", "op": "plugin/op"},
            {"id": "s1", "kind": "deterministic", "op": "plugin/op2"},
        ],
    }
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_deterministic_stage_requires_op():
    bad = {**MINIMAL, "stages": [{"id": "s1", "kind": "deterministic"}]}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_invalid_stage_kind_rejected():
    bad = {**MINIMAL, "stages": [{"id": "s1", "kind": "bogus"}]}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_on_fail_retry_n_accepted():
    ok = {
        **MINIMAL,
        "stages": [{"id": "s1", "kind": "deterministic", "op": "p/o", "on_fail": "retry:3"}],
    }
    spec = parse_flow_spec(ok)
    assert spec.stages[0].on_fail == "retry:3"


def test_on_fail_bad_value_rejected():
    bad = {
        **MINIMAL,
        "stages": [{"id": "s1", "kind": "deterministic", "op": "p/o", "on_fail": "maybe"}],
    }
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_repeat_until_bad_target_stage_rejected():
    bad = {
        **MINIMAL,
        "stages": [
            {
                "id": "s1",
                "kind": "deterministic",
                "op": "p/o",
                "repeat_until": {"field": "passed", "equals": True, "max_rounds": 1, "on_retry_stage": "nope"},
            }
        ],
    }
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_repeat_until_valid_jump_target():
    ok = {
        **MINIMAL,
        "stages": [
            {"id": "compose", "kind": "deterministic", "op": "p/compose"},
            {
                "id": "validate",
                "kind": "deterministic",
                "op": "p/validate",
                "repeat_until": {
                    "field": "passes",
                    "equals": True,
                    "max_rounds": 2,
                    "on_retry_stage": "compose",
                },
            },
        ],
    }
    spec = parse_flow_spec(ok)
    assert spec.stages[1].repeat_until.on_retry_stage == "compose"
    assert spec.stages[1].repeat_until.max_rounds == 2


def test_claims_parsed():
    ok = {
        **MINIMAL,
        "claims": {
            "goal_classes": ["bake_for_job"],
            "any_keywords": ["Portfolio", "BAKE"],
            "deny_keywords": ["discover"],
        },
    }
    spec = parse_flow_spec(ok)
    assert spec.claims.goal_classes == ("bake_for_job",)
    # keywords normalized to lowercase for matching
    assert spec.claims.any_keywords == ("portfolio", "bake")
    assert spec.claims.matches(goal="please bake this portfolio", goal_class=None)
    assert not spec.claims.matches(goal="discover the portfolio", goal_class=None)


def test_claims_unknown_key_rejected():
    bad = {**MINIMAL, "claims": {"bogus": 1}}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_max_steps_and_seconds_validated():
    bad = {**MINIMAL, "stages": [{"id": "s1", "kind": "deterministic", "op": "p/o", "max_steps": 0}]}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)
    bad2 = {**MINIMAL, "stages": [{"id": "s1", "kind": "deterministic", "op": "p/o", "max_seconds": -1}]}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad2)


def test_requires_provides_roundtrip():
    ok = {**MINIMAL, "requires": ["have:job_signals"], "provides": ["did:bake"]}
    spec = parse_flow_spec(ok)
    assert spec.requires == ("have:job_signals",)
    assert spec.provides == ("did:bake",)


# --------------------------------------------------------------------------- #
# Model-role stage fields (S8b — core_graph/model_roles/) #
# --------------------------------------------------------------------------- #

AGENTIC_MINIMAL = {
    "flow_id": "f1",
    "name": "Flow One",
    "stages": [{"id": "s1", "kind": "agentic"}],
}


def test_agentic_stage_effort_accepted():
    ok = {**AGENTIC_MINIMAL, "stages": [{"id": "s1", "kind": "agentic", "effort": "high"}]}
    spec = parse_flow_spec(ok)
    assert spec.stages[0].effort == "high"
    assert spec.stages[0].model is None


def test_agentic_stage_model_accepted():
    ok = {**AGENTIC_MINIMAL, "stages": [{"id": "s1", "kind": "agentic", "model": "strongest"}]}
    spec = parse_flow_spec(ok)
    assert spec.stages[0].model == "strongest"
    assert spec.stages[0].effort is None


def test_stage_effort_outside_vocabulary_rejected():
    bad = {**AGENTIC_MINIMAL, "stages": [{"id": "s1", "kind": "agentic", "effort": "extreme"}]}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_stage_model_and_effort_together_rejected():
    bad = {
        **AGENTIC_MINIMAL,
        "stages": [{"id": "s1", "kind": "agentic", "model": "strongest", "effort": "high"}],
    }
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_deterministic_stage_with_model_rejected():
    bad = {**MINIMAL, "stages": [{"id": "s1", "kind": "deterministic", "op": "p/o", "model": "strongest"}]}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_deterministic_stage_with_effort_rejected():
    bad = {**MINIMAL, "stages": [{"id": "s1", "kind": "deterministic", "op": "p/o", "effort": "low"}]}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)


def test_flow_level_effort_accepted():
    ok = {**AGENTIC_MINIMAL, "effort": "medium"}
    spec = parse_flow_spec(ok)
    assert spec.effort == "medium"


def test_flow_level_effort_outside_vocabulary_rejected():
    bad = {**AGENTIC_MINIMAL, "effort": "nope"}
    with pytest.raises(FlowSpecError):
        parse_flow_spec(bad)
