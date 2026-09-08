"""
Unit tests for the GOAP planner.
"""

from core_graph.goap.world_state import WorldState
from core_graph.goap.derive import derive_actions
from core_graph.goap.planner import plan, GoapPlan


def test_goal_already_satisfied():
    """
    Test 1: Goal already satisfied by seed world -> plan() returns empty GoapPlan.
    """
    world = WorldState(["have:record_id"])
    goal = ["have:record_id"]
    actions = []

    result = plan(goal, world, actions)
    assert isinstance(result, GoapPlan)
    assert result.actions == []
    assert result.steps == []
    assert result.cost == 0.0


def test_single_applicable_action():
    """
    Test 2: Single applicable action reaching the goal -> plan with 1 step, no bindings.
    """
    candidates = [
        {
            "operation_id": "createRecord",
            "plugin_id": "test_plugin",
            "description": "Create a new record record",
            "method": "POST",
            "path": "/records",
            "parameters": {},
        }
    ]
    actions = derive_actions(candidates)
    world = WorldState()
    goal = ["have:record_id"]

    result = plan(goal, world, actions)
    assert result is not None
    assert len(result.actions) == 1
    assert result.actions[0].operation_id == "createRecord"
    assert result.cost == 1.0

    assert len(result.steps) == 1
    step = result.steps[0]
    assert step["operation_id"] == "createRecord"
    assert step["arg_bindings"] == {}
    assert step["depends_on"] is None


def test_two_step_chain():
    """
    Test 3: Two-step chain (createRecord + sendMessage) with arg_bindings.
    """
    candidates = [
        {
            "operation_id": "createRecord",
            "plugin_id": "test_plugin",
            "description": "Create a new record record",
            "method": "POST",
            "path": "/records",
            "parameters": {},
        },
        {
            "operation_id": "sendMessage",
            "plugin_id": "test_plugin",
            "description": "Send a message to a record",
            "method": "POST",
            "path": "/messages",
            "parameters": {
                "record_id": {"required": True, "type": "string"},
                "message_body": {"required": True, "type": "string"},
            },
        },
    ]
    actions = derive_actions(candidates)
    world = WorldState(["have:message_body"])
    goal = ["did:sendMessage"]

    result = plan(goal, world, actions)
    assert result is not None
    assert len(result.actions) == 2
    assert result.actions[0].operation_id == "createRecord"
    assert result.actions[1].operation_id == "sendMessage"
    assert result.cost == 2.0

    assert len(result.steps) == 2

    # Step 0: createRecord
    step_0 = result.steps[0]
    assert step_0["operation_id"] == "createRecord"
    assert step_0["arg_bindings"] == {}
    assert step_0["depends_on"] is None

    # Step 1: sendMessage
    step_1 = result.steps[1]
    assert step_1["operation_id"] == "sendMessage"
    # sendMessage needs have:record_id and have:message_body.
    # have:message_body is a seed fact, so it's skipped.
    # have:record_id is produced by createRecord (step 0), mapping to 'id'.
    assert step_1["arg_bindings"] == {"record_id": "$steps[0].id"}
    assert step_1["depends_on"] == 0


def test_unreachable_goal():
    """
    Test 4: Unreachable goal (no action produces the fact, not in seed) -> plan() returns None.
    """
    candidates = [
        {
            "operation_id": "createRecord",
            "plugin_id": "test_plugin",
            "description": "Create a new record record",
            "method": "POST",
            "path": "/records",
            "parameters": {},
        }
    ]
    actions = derive_actions(candidates)
    world = WorldState()
    goal = ["have:message_body"]

    result = plan(goal, world, actions)
    assert result is None


def test_is_fast_path_carried_over():
    """
    Test 5: is_fast_path is carried from the candidate into the step dict.
    """
    candidates = [
        {
            "operation_id": "createRecord",
            "plugin_id": "test_plugin",
            "description": "Create a new record record",
            "method": "POST",
            "path": "/records",
            "parameters": {},
            "is_fast_path": True,
        }
    ]
    actions = derive_actions(candidates)
    world = WorldState()
    goal = ["have:record_id"]

    result = plan(goal, world, actions)
    assert result is not None
    assert len(result.steps) == 1
    assert result.steps[0]["is_fast_path"] is True


def test_cyclic_effects_prevention():
    """
    Test 6: The planner does not loop forever on cyclic effects and returns None.
    """
    # A requires have:fact_b and produces have:fact_a
    # B requires have:fact_a and produces have:fact_b
    candidates = [
        {
            "operation_id": "actionA",
            "plugin_id": "test_plugin",
            "description": "Action A",
            "method": "GET",
            "path": "/a",
            "parameters": {
                "fact_b": {"required": True},
            },
        },
        {
            "operation_id": "actionB",
            "plugin_id": "test_plugin",
            "description": "Action B",
            "method": "GET",
            "path": "/b",
            "parameters": {
                "fact_a": {"required": True},
            },
        },
    ]
    actions = derive_actions(candidates)
    world = WorldState(["have:fact_b"])
    # Goal is impossible since nothing produces have:goal_fact
    goal = ["have:goal_fact"]

    result = plan(goal, world, actions, max_steps=8)
    assert result is None
