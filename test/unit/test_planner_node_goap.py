"""
Unit tests for the GOAP integration module and planner node helpers.
"""

import pytest
import core_graph.node.helpers.gating
core_graph.node.helpers.gating.LONG_CHAIN_THRESHOLD = 3
core_graph.node.helpers.gating.CONFIDENCE_EXECUTE_THRESHOLD = 0.70
core_graph.node.helpers.gating.CONFIDENCE_CONFIRM_THRESHOLD = 0.50

from core_graph.goap.integrate import (
    parse_goal_extraction,
    build_seed_world,
    goap_plan_from_goal,
    goap_steps_to_state,
)
from core_graph.goap.planner import GoapPlan
from core_graph.graph import compute_gate


def test_parse_goal_extraction():
    # 1. parse_goal_extraction(None) == ([], [], "") ; tolerant of partial dicts.
    assert parse_goal_extraction(None) == ([], [], "")
    assert parse_goal_extraction({}) == ([], [], "")
    assert parse_goal_extraction({"goal": "not a list"}) == ([], [], "")
    assert parse_goal_extraction({"seed_facts": "not a list"}) == ([], [], "")
    
    parsed = {
        "goal": ["did:sendMessage"],
        "seed_facts": ["have:message_body"],
        "intent": "Send a message",
    }
    assert parse_goal_extraction(parsed) == (["did:sendMessage"], ["have:message_body"], "Send a message")

    partial = {
        "goal": ["did:sendMessage"],
    }
    assert parse_goal_extraction(partial) == (["did:sendMessage"], [], "")


def test_build_seed_world():
    # 2. build_seed_world(["have:message_body"], {"record_id": 5}) satisfies ["have:message_body", "have:record_id"].
    world = build_seed_world(["have:message_body"], {"record_id": 5})
    assert world.satisfies(["have:message_body", "have:record_id"])
    assert world.has("have:message_body")
    assert world.has("have:record_id")


def test_goap_plan_from_goal_chain():
    # 3. goap_plan_from_goal for the create→send chain (synthetic candidates:
    #    createRecord POST /records ; sendMessage POST /messages with required
    #    record_id + message_body) with seed_facts=["have:message_body"],
    #    goal=["did:sendMessage"] → returns a GoapPlan whose steps[1]["arg_bindings"]
    #    == {"record_id": "$steps[0].id"} and steps[1]["depends_on"] == 0.
    candidates = [
        {
            "operation_id": "createRecord",
            "plugin_id": "test_plugin",
            "description": "Create record",
            "method": "POST",
            "path": "/records",
            "parameters": {},
        },
        {
            "operation_id": "sendMessage",
            "plugin_id": "test_plugin",
            "description": "Send message",
            "method": "POST",
            "path": "/messages",
            "parameters": {
                "record_id": {"required": True, "type": "string"},
                "message_body": {"required": True, "type": "string"},
            },
        },
    ]
    goal = ["did:sendMessage"]
    seed_facts = ["have:message_body"]
    working_memory = {}

    gplan = goap_plan_from_goal(goal, seed_facts, working_memory, candidates)
    assert gplan is not None
    assert len(gplan.steps) == 2
    assert gplan.steps[0]["operation_id"] == "createRecord"
    assert gplan.steps[1]["operation_id"] == "sendMessage"
    assert gplan.steps[1]["arg_bindings"] == {"record_id": "$steps[0].id"}
    assert gplan.steps[1]["depends_on"] == 0


def test_goap_plan_from_goal_empty():
    # 4. goap_plan_from_goal(goal=[], ...) returns None (empty goal → fall back).
    candidates = [
        {
            "operation_id": "createRecord",
            "plugin_id": "test_plugin",
            "description": "Create record",
            "method": "POST",
            "path": "/records",
            "parameters": {},
        }
    ]
    gplan = goap_plan_from_goal([], [], {}, candidates)
    assert gplan is None


def test_goap_plan_from_goal_unreachable():
    # 5. goap_plan_from_goal with an unreachable goal returns None.
    candidates = [
        {
            "operation_id": "createRecord",
            "plugin_id": "test_plugin",
            "description": "Create record",
            "method": "POST",
            "path": "/records",
            "parameters": {},
        }
    ]
    # sendMessage is goal, but no candidate has it
    gplan = goap_plan_from_goal(["did:sendMessage"], [], {}, candidates)
    assert gplan is None


def test_goap_steps_to_state():
    # 6. goap_steps_to_state(gplan, candidates): "plan" non-empty, "current_step_index"
    #    == 0, "selected" is the candidate dict for steps[0], "instruction_set" equals
    #    the steps, and the fragment does NOT contain "response".
    candidates = [
        {
            "operation_id": "createRecord",
            "plugin_id": "test_plugin",
            "description": "Create record",
            "method": "POST",
            "path": "/records",
            "parameters": {},
            "score": 0.9,
        },
        {
            "operation_id": "sendMessage",
            "plugin_id": "test_plugin",
            "description": "Send message",
            "method": "POST",
            "path": "/messages",
            "parameters": {},
            "score": 0.85,
        },
    ]
    
    # Let's create a synthetic GoapPlan
    from core_graph.goap.derive import derive_actions
    from core_graph.goap.world_state import WorldState
    from core_graph.goap import planner
    
    actions = derive_actions(candidates)
    world = WorldState()
    gplan = planner.plan(["have:record_id"], world, actions)
    assert gplan is not None

    frag = goap_steps_to_state(gplan, candidates)
    
    assert len(frag["plan"]) > 0
    assert frag["current_step_index"] == 0
    assert frag["selected"] == candidates[0]
    assert frag["instruction_set"] == frag["plan"]
    assert "response" not in frag
    assert frag["workflow_name"] == "goap-plan"


def test_compute_gate_long_chain_invariant():
    # 7. INVARIANT (long-chain): compute_gate(0.95, None, 4)[1] == "confirm" (import
    #    compute_gate from core_graph.graph) — guards that a 4-step GOAP plan confirms.
    confidence, decision = compute_gate(0.95, None, step_count=4)
    assert decision == "confirm"


def test_parse_goal_extraction_extended():
    from core_graph.goap.integrate import parse_goal_extraction_extended

    # None and empty dict handling
    assert parse_goal_extraction_extended(None) == ([], [], "", None, None, None)
    assert parse_goal_extraction_extended({}) == ([], [], "", None, None, None)

    # Partial and standard dictionary parsing
    parsed = {
        "goal": ["did:sendMessage"],
        "seed_facts": ["have:message_body"],
        "intent": "Send a message",
        "confidence": 0.85,
        "clarifying_questions": [
            {
                "header": "Action",
                "question": "Which action?",
                "multiSelect": False,
                "options": [{"label": "SMS", "value": "sendMessage"}]
            }
        ],
        "fan_out": {
            "operation": "did:sendMessage",
            "over": "$steps[0].items",
            "count": 5
        }
    }
    goal, seeds, intent, confidence, questions, fan_out = parse_goal_extraction_extended(parsed)
    assert goal == ["did:sendMessage"]
    assert seeds == ["have:message_body"]
    assert intent == "Send a message"
    assert confidence == 0.85
    assert fan_out == {"operation": "did:sendMessage", "over": "$steps[0].items", "count": 5}
    assert len(questions) == 1
    assert questions[0]["header"] == "Action"


def test_build_clarify_questions():
    from core_graph.clarify import build_clarify_questions

    # 1. Test LLM questions present
    llm_q = [
        {
            "header": "ActionCategoryVeryLong", # Should be truncated/clamped to 12 chars
            "question": "Is this correct?",
            "multiSelect": True,
            "options": [
                {"label": "Option A", "description": "Desc A", "value": "valA"},
                {"label": "Option B", "description": "Desc B", "value": "valB"}
            ]
        }
    ]
    res = build_clarify_questions(llm_questions=llm_q)
    assert len(res) == 1
    assert res[0]["header"] == "ActionCatego" # 12 chars
    assert res[0]["question"] == "Is this correct?"
    assert res[0]["multiSelect"] is True
    assert len(res[0]["options"]) == 2
    assert res[0]["options"][0]["value"] == "valA"

    # 2. Test missing parameters fallback
    missing = ["record_id", "message_body"]
    res = build_clarify_questions(missing_params=missing)
    assert len(res) == 2
    assert res[0]["header"] == "record_id"
    assert res[0]["question"] == "Provide record_id"
    assert res[0]["options"] == []
    assert res[0]["value"] == "record_id"

    # 3. Test candidates fallback
    candidates = [
        {
            "operation_id": "sendMessage",
            "method": "post",
            "path": "/messages",
            "description": "Send SMS",
            "score": 0.8
        }
    ]
    res = build_clarify_questions(candidates=candidates)
    assert len(res) == 1
    assert res[0]["header"] == "Action"
    # Candidate fallback is now multi-select (pick a whole workflow at once).
    assert res[0]["multiSelect"] is True
    assert res[0]["question"] == "Which operation(s) should I use? Select every step you want, in order."
    assert len(res[0]["options"]) == 1
    assert res[0]["options"][0]["label"] == "Send Message"
    assert res[0]["options"][0]["description"] == "POST /messages — Send SMS"
    assert res[0]["options"][0]["value"] == "sendMessage"

