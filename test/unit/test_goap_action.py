"""
Unit tests for GOAP Action mapping and derivation.
"""

import pytest
from core_graph.goap.action import GoapAction
from core_graph.goap.derive import (
    _path_params,
    _required_params,
    _resource_of,
    derive_action,
    derive_actions,
)


def test_post_create_record():
    """
    Test 1: POST createRecord at path "/records" -> effects include "have:record_id"
    (field "id") and "have:record" and "did:createRecord"; no preconditions.
    """
    candidate = {
        "operation_id": "createRecord",
        "plugin_id": "test_plugin",
        "description": "Create a new record record",
        "method": "POST",
        "path": "/records",
        "parameters": {},
    }
    action = derive_action(candidate)
    
    assert action.operation_id == "createRecord"
    assert action.plugin_id == "test_plugin"
    assert action.intent == "Create a new record record"
    assert action.preconditions == frozenset()
    
    # Check effects
    assert "have:record_id" in action.effects
    assert "have:record" in action.effects
    assert "have:id" in action.effects
    assert "did:createRecord" in action.effects
    assert len(action.effects) == 4
    
    # Check effect_field_map
    field_map = action.effect_field_map()
    assert field_map.get("have:record_id") == "id"
    assert field_map.get("have:id") == "id"
    assert field_map.get("have:record") == "record"


def test_get_record_path_template():
    """
    Test 2: GET getRecord at path_template "/records/{record_id}" -> preconditions
    == {"have:record_id"} ; effects include "have:record".
    """
    candidate = {
        "operation_id": "getRecord",
        "plugin_id": "test_plugin",
        "description": "Get a record by ID",
        "method": "GET",
        "path_template": "/records/{record_id}",
        "parameters": {},
    }
    action = derive_action(candidate)
    
    assert action.preconditions == frozenset(["have:record_id"])
    assert "have:record" in action.effects
    assert "did:getRecord" in action.effects
    assert "have:record_id" not in action.effects
    assert action.effect_field_map().get("have:record") == "record"


def test_flat_parameters():
    """
    Test 3: Flat parameters {"phone": {"required": True}, "note": {"required": False}}
    -> preconditions include "have:phone" and NOT "have:note".
    """
    candidate = {
        "operation_id": "updateRecordPhone",
        "plugin_id": "test_plugin",
        "method": "PATCH",
        "path": "/records",
        "parameters": {
            "phone": {"required": True, "type": "string"},
            "note": {"required": False, "type": "string"},
        },
    }
    action = derive_action(candidate)
    assert "have:phone" in action.preconditions
    assert "have:note" not in action.preconditions


def test_json_schema_parameters():
    """
    Test 4: JSON-schema parameters {"properties": {...}, "required": ["body"]}
    -> preconditions include "have:body".
    """
    candidate = {
        "operation_id": "sendMessage",
        "plugin_id": "test_plugin",
        "method": "POST",
        "path": "/messages",
        "parameters": {
            "properties": {
                "body": {"type": "string"},
                "subject": {"type": "string"},
            },
            "required": ["body"],
        },
    }
    action = derive_action(candidate)
    assert "have:body" in action.preconditions
    assert "have:subject" not in action.preconditions


def test_effect_field_map_post_create():
    """
    Test 5: effect_field_map() maps "have:record_id" -> "id" for the POST create case.
    """
    candidate = {
        "operation_id": "createRecord",
        "plugin_id": "test_plugin",
        "method": "POST",
        "path": "/records",
    }
    action = derive_action(candidate)
    field_map = action.effect_field_map()
    assert field_map.get("have:record_id") == "id"
    assert field_map.get("have:record") == "record"


def test_hashable_and_frozen():
    """
    Test 6: GoapAction is hashable and frozen (constructing a set of two identical
    actions dedupes to 1; candidate dict does not break hashing).
    """
    candidate1 = {
        "operation_id": "createRecord",
        "plugin_id": "test_plugin",
        "method": "POST",
        "path": "/records",
    }
    candidate2 = {
        "operation_id": "createRecord",
        "plugin_id": "test_plugin",
        "method": "POST",
        "path": "/records",
        "score": 0.99, # different metadata
    }
    
    action1 = derive_action(candidate1)
    action2 = derive_action(candidate2)
    
    # They should be equal and have the same hash because candidate is ignored
    assert action1 == action2
    assert hash(action1) == hash(action2)
    
    # Deduplication in a set
    actions_set = {action1, action2}
    assert len(actions_set) == 1
    
    # Try to mutate a frozen dataclass should raise exception
    with pytest.raises(Exception):
        action1.cost = 2.0  # type: ignore


def test_derive_actions_order():
    """
    Test 7: derive_actions over a 2-candidate list returns 2 GoapActions in order.
    """
    candidates = [
        {
            "operation_id": "createRecord",
            "plugin_id": "test_plugin",
            "method": "POST",
            "path": "/records",
        },
        {
            "operation_id": "getRecord",
            "plugin_id": "test_plugin",
            "method": "GET",
            "path_template": "/records/{record_id}",
        }
    ]
    actions = derive_actions(candidates)
    assert len(actions) == 2
    assert actions[0].operation_id == "createRecord"
    assert actions[1].operation_id == "getRecord"
