"""
Unit tests for the composed DynamicAPIState (repo-polish phase 5).

DynamicAPIState.__annotations__ only reflects the class's own body (empty —
it's composed by inheritance from four sub-states), so field-presence checks
here read the merged view via typing.get_type_hints(..., include_extras=True)
instead — the same mechanism LangGraph itself uses to resolve the state
schema, so a test using it is testing what the graph actually sees.
"""

import operator
import typing

from langgraph.graph.message import add_messages

from core_graph.goap.goal_loop import reset_turn_fragment
from core_graph.states import DynamicAPIState


def _hints() -> dict:
    return typing.get_type_hints(DynamicAPIState, include_extras=True)


def test_dynamic_api_state_import():
    """Verify that DynamicAPIState can be imported without errors."""
    assert DynamicAPIState is not None


def test_dynamic_api_state_new_annotations():
    """Verify that the new agentic session and goal-loop fields are declared in DynamicAPIState."""
    annotations = _hints()
    new_fields = [
        "session_id",
        "working_memory",
        "last_summary",
        "goal",
        "iterations",
        "max_iterations",
        "goal_loop_decision",
    ]
    for field in new_fields:
        assert field in annotations


def test_dynamic_api_state_existing_annotations():
    """Verify that the existing crucial state fields are still declared in DynamicAPIState."""
    annotations = _hints()
    existing_fields = [
        "user_query",
        "candidates",
        "plan",
        "messages",
        "triage_mode",
        "summary",
    ]
    for field in existing_fields:
        assert field in annotations


def test_dynamic_api_state_runtime_assignment():
    """Verify that a plain dict literal with new and existing fields is valid at runtime."""
    state_dict: DynamicAPIState = {
        "user_query": "hello agent",
        "messages": [],
        "session_id": "session_123",
        "working_memory": {"record_id": 456},
        "last_summary": "Prior recap here",
        "goal": "Retrieve medical history",
        "iterations": 0,
        "max_iterations": 5,
    }

    assert state_dict["user_query"] == "hello agent"
    assert state_dict["session_id"] == "session_123"
    assert state_dict["working_memory"] == {"record_id": 456}
    assert state_dict["last_summary"] == "Prior recap here"
    assert state_dict["goal"] == "Retrieve medical history"
    assert state_dict["iterations"] == 0
    assert state_dict["max_iterations"] == 5


def test_dynamic_api_state_is_composed_from_four_sub_states():
    """Regression guard for the phase-5 TypedDict-inheritance split.

    TypedDict raises TypeError on issubclass()/isinstance() ("does not
    support instance and class checks"), and its normal __mro__ collapses
    every TypedDict to (cls, dict, object) — so the only reliable runtime
    signal that DynamicAPIState is composed from these four bases (rather
    than redeclaring their fields flat) is typing's __orig_bases__.
    """
    from core_graph.states import (
        AuditSubState,
        PlanExecutionSubState,
        TriageSubState,
        WorkflowSubState,
    )

    assert set(DynamicAPIState.__orig_bases__) == {
        TriageSubState,
        PlanExecutionSubState,
        WorkflowSubState,
        AuditSubState,
    }
    # Inheritance-only: every declared field traces back to one of the four
    # sub-states — DynamicAPIState adds none of its own. (Note: unlike some
    # Python versions, this interpreter's TypedDict.__annotations__ already
    # merges inherited fields, so this checks the union rather than emptiness.)
    sub_state_fields = (
        set(TriageSubState.__annotations__)
        | set(PlanExecutionSubState.__annotations__)
        | set(WorkflowSubState.__annotations__)
        | set(AuditSubState.__annotations__)
    )
    assert set(_hints()) == sub_state_fields


def test_dynamic_api_state_has_exactly_two_reduced_fields():
    """Only `messages` (add_messages) and `model_audit` (operator.add) may carry a reducer.

    A bare typing.get_type_hints() call (without include_extras=True) would
    silently strip Annotated metadata and turn a reduced field into
    last-write-wins — this test would catch that regression too, since it
    would then find zero reduced fields instead of two.
    """
    annotations = _hints()
    reduced = {
        name: typing.get_args(hint)[1]
        for name, hint in annotations.items()
        if typing.get_origin(hint) is typing.Annotated
    }
    assert set(reduced) == {"messages", "model_audit"}
    assert reduced["messages"] is add_messages
    assert reduced["model_audit"] is operator.add


def test_reset_turn_fragment_keys_are_a_subset_of_declared_state_keys():
    """Every key reset_turn_fragment clears must actually be a declared state field."""
    annotations = _hints()
    reset_keys = set(reset_turn_fragment({}).keys())
    assert reset_keys <= set(annotations)
