"""
Unit tests for core/goal_spec.py.
"""

import pytest
from core.goal_spec import TaskType, TaskSpec, GoalSpec


def test_task_type_enum_members():
    """Verify that TaskType enum has exactly 5 members with correct string values."""
    assert len(TaskType) == 5
    assert TaskType.DATA_RETRIEVAL == "data_retrieval"
    assert TaskType.REASONING == "reasoning"
    assert TaskType.CODE_EXECUTION == "code_execution"
    assert TaskType.FORMATTING == "formatting"
    assert TaskType.API_CALL == "api_call"


def test_task_spec_instantiation():
    """Verify that TaskSpec instantiates with required fields and defaults are set correctly."""
    task = TaskSpec(
        id="task_1",
        task_type=TaskType.DATA_RETRIEVAL,
        intent="Fetch record by ID",
        depends_on=[],
    )

    assert task.id == "task_1"
    assert task.task_type == TaskType.DATA_RETRIEVAL
    assert task.intent == "Fetch record by ID"
    assert task.depends_on == []
    assert task.model is None
    assert task.tools == []


def test_goal_spec_instantiation():
    """Verify that GoalSpec instantiates with correct types and context defaults to an empty dict."""
    task1 = TaskSpec(
        id="task_1",
        task_type=TaskType.DATA_RETRIEVAL,
        intent="Fetch record data",
        depends_on=[],
    )
    task2 = TaskSpec(
        id="task_2",
        task_type=TaskType.REASONING,
        intent="Analyze record data",
        depends_on=["task_1"],
    )

    goal = GoalSpec(
        raw_goal="Analyze data for record 123",
        intent="record_data_analysis",
        tasks=[task1, task2],
        success_criteria="Analysis is generated",
    )

    assert goal.raw_goal == "Analyze data for record 123"
    assert goal.intent == "record_data_analysis"
    assert goal.tasks == [task1, task2]
    assert goal.success_criteria == "Analysis is generated"
    assert goal.context == {}


def test_task_spec_depends_on_contains_strings():
    """Verify that depends_on field is a list of strings (not task objects)."""
    task = TaskSpec(
        id="task_3",
        task_type=TaskType.API_CALL,
        intent="Make API request",
        depends_on=["task_1", "task_2"],
    )

    assert isinstance(task.depends_on, list)
    assert all(isinstance(dep, str) for dep in task.depends_on)
    assert task.depends_on == ["task_1", "task_2"]
