import pytest
from core_graph.workflow_yaml import parse_yaml_to_plan, serialize_plan_to_yaml, WorkflowParseError

# Candidate routes fixture
CANDIDATES = [
    {
        "operation_id": "create_record",
        "plugin_id": "fake_plugin",
        "is_fast_path": True,
        "description": "Create record",
        "parameters": {"given_name": {"type": "string"}, "last_name": {"type": "string"}}
    },
    {
        "operation_id": "send_message",
        "plugin_id": "fake_plugin",
        "is_fast_path": True,
        "description": "Send message",
        "parameters": {"record_id": {"type": "string"}, "body": {"type": "string"}}
    }
]


def test_parse_yaml_to_plan_valid():
    yaml_str = """
name: test-workflow
steps:
  - operation_id: create_record
    plugin_id: fake_plugin
    intent: create a new record
    args:
      given_name: John
      last_name: Doe
  - operation_id: send_message
    plugin_id: fake_plugin
    intent: send confirmation message
    args:
      body: Welcome!
    arg_bindings:
      record_id: $steps[0].id
parallel_groups:
  - [0]
  - [1]
outputs:
  record_id: $steps[0].id
"""
    plan, groups, outputs = parse_yaml_to_plan(yaml_str, CANDIDATES)
    
    assert len(plan) == 2
    assert plan[0]["operation_id"] == "create_record"
    assert plan[0]["plugin_id"] == "fake_plugin"
    assert plan[0]["is_fast_path"] is True
    assert plan[0]["args"]["given_name"] == "John"
    
    assert plan[1]["operation_id"] == "send_message"
    assert plan[1]["arg_bindings"]["record_id"] == "$steps[0].id"
    
    assert groups == [[0], [1]]
    assert outputs == {"record_id": "$steps[0].id"}


def test_parse_yaml_to_plan_unknown_op():
    yaml_str = """
steps:
  - operation_id: unknown_operation
    plugin_id: fake_plugin
    intent: what am I doing?
"""
    with pytest.raises(WorkflowParseError) as exc_info:
        parse_yaml_to_plan(yaml_str, CANDIDATES)
    assert "unknown operation_id" in str(exc_info.value)


def test_parse_yaml_to_plan_malformed():
    yaml_str = """
steps:
  - operation_id: create_record
    plugin_id: fake_plugin
  - malformed_yaml_here
    args: {
"""
    with pytest.raises(WorkflowParseError) as exc_info:
        parse_yaml_to_plan(yaml_str, CANDIDATES)
    assert "Malformed YAML" in str(exc_info.value)


def test_parse_yaml_to_plan_datetime_preservation():
    yaml_str = """
steps:
  - operation_id: create_record
    plugin_id: fake_plugin
    intent: test datetime preservation
    args:
      created_at: 2025-08-07T09:00:00Z
      birthdate: 1990-01-01
"""
    plan, _, _ = parse_yaml_to_plan(yaml_str, CANDIDATES)
    assert isinstance(plan[0]["args"]["created_at"], str)
    assert plan[0]["args"]["created_at"] == "2025-08-07T09:00:00Z"
    assert isinstance(plan[0]["args"]["birthdate"], str)
    assert plan[0]["args"]["birthdate"] == "1990-01-01"


def test_serialize_plan_to_yaml_roundtrip():
    original_plan = [
        {
            "operation_id": "create_record",
            "plugin_id": "fake_plugin",
            "intent": "create a test record",
            "args": {"given_name": "Alice", "last_name": "Smith"},
            "arg_bindings": {},
            "depends_on": None,
        },
        {
            "operation_id": "send_message",
            "plugin_id": "fake_plugin",
            "intent": "send a test message",
            "args": {"body": "Hello Alice!"},
            "arg_bindings": {"record_id": "$steps[0].id"},
            "depends_on": [0],
        }
    ]
    parallel_groups = [[0], [1]]
    outputs = {"record_id": "$steps[0].id"}

    yaml_str = serialize_plan_to_yaml(original_plan, parallel_groups, outputs)
    assert "steps:" in yaml_str
    assert "create_record" in yaml_str
    assert "send_message" in yaml_str

    parsed_plan, parsed_groups, parsed_outputs = parse_yaml_to_plan(yaml_str, CANDIDATES)

    assert len(parsed_plan) == len(original_plan)
    assert parsed_groups == parallel_groups
    assert parsed_outputs == outputs

    for orig, parsed in zip(original_plan, parsed_plan):
        assert parsed["operation_id"] == orig["operation_id"]
        assert parsed["args"] == orig["args"]
        assert parsed["arg_bindings"] == orig["arg_bindings"]
        assert parsed["depends_on"] == orig["depends_on"]


def test_parse_yaml_to_plan_tab_indented():
    yaml_str = "steps:\n\t- operation_id: create_record"
    with pytest.raises(WorkflowParseError) as exc_info:
        parse_yaml_to_plan(yaml_str, CANDIDATES)
    assert "Malformed YAML" in str(exc_info.value)



