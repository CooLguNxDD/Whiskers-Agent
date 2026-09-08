"""Integration test: triage_node escalating across a two-rung ladder (flag on).

Proves the S5 cutover end to end — garbage JSON on rung 0 escalates to rung 1
purely from a registered ModelRoleSpec, with no branching in triage.py itself.
"""

from __future__ import annotations

import json

import pytest

from core_graph.model_roles.registry import _reset_model_roles_for_tests, get_model_role_registry
from core_graph.model_roles.role_spec import parse_model_role_spec
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.triage import make_triage_node


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _TwoStageLLM:
    """First call returns garbage, second returns a valid triage payload."""

    def __init__(self):
        self.calls = 0
        self.model = "stub"

    async def ainvoke(self, msgs):
        self.calls += 1
        if self.calls == 1:
            return _FakeResp("not json at all")
        return _FakeResp(json.dumps({"mode": "specialist"}))


def _ctx(llm):
    return GraphRuntimeContext(llm=llm, context_params={}, api_url="", route_registry=None, checkpointer=None)


TWO_RUNG_TRIAGE = {
    "role_id": "triage",
    "ladder": [{"selector": "fast"}, {"selector": "strongest"}],
    "validate": {
        "require_json": True,
        "enum_field": "mode",
        "enum_values": ["chat", "classic", "specialist", "task"],
    },
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr("utils.server_config.MODEL_ROLES_ENABLED", True)
    _reset_model_roles_for_tests()
    yield
    _reset_model_roles_for_tests()


@pytest.mark.asyncio
async def test_escalates_to_rung_1_on_bad_json_and_records_audit():
    reg = get_model_role_registry()
    reg.set_db_override("triage", parse_model_role_spec(TWO_RUNG_TRIAGE, owner="db"))

    llm = _TwoStageLLM()
    node = make_triage_node(_ctx(llm))
    out = await node({"user_query": "bake my portfolio"})

    assert out["triage_mode"] == "specialist"
    assert llm.calls == 2
    audit = out["model_audit"][0]
    assert audit["escalated"] is True
    assert len(audit["attempts"]) == 2
    assert audit["attempts"][0]["status"] == "invalid"
    assert audit["attempts"][1]["status"] == "ok"
    assert out["token_usage"]["model_usage"]["stub"]["calls"] == 2


class _AlwaysGarbageLLM:
    def __init__(self):
        self.calls = 0
        self.model = "stub"

    async def ainvoke(self, msgs):
        self.calls += 1
        return _FakeResp("still not json")


@pytest.mark.asyncio
async def test_all_rungs_fail_reaches_heuristic_fallback():
    reg = get_model_role_registry()
    reg.set_db_override("triage", parse_model_role_spec(TWO_RUNG_TRIAGE, owner="db"))

    llm = _AlwaysGarbageLLM()
    node = make_triage_node(_ctx(llm))
    out = await node({"user_query": "please bake my portfolio now"})

    # ctx_llm terminal fallback re-runs against the same stub (still garbage),
    # so extraction still fails -> node falls through to _heuristic_triage.
    assert out["triage_mode"] == "specialist"  # heuristic: "bake" + "portfolio" hint
    assert out["model_audit"][0]["status"] == "exhausted"
