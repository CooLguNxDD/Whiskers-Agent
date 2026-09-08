import pytest

from core.goal_spec import GoalSpec, TaskSpec, TaskType
from core.model_conductor import (
    DEFAULT_TASK_MODEL_MAP,
    _load_task_model_map,
    assign_models,
)
import core.model_conductor


def test_assign_models_data_retrieval():
    # 1. assign_models sets correct model for DATA_RETRIEVAL task
    task = TaskSpec(
        id="task_1",
        task_type=TaskType.DATA_RETRIEVAL,
        intent="Fetch data",
        depends_on=[],
    )
    goal = GoalSpec(
        raw_goal="Fetch some data",
        intent="fetching",
        tasks=[task],
        success_criteria="Data fetched",
    )
    
    result = assign_models(goal)
    assert result.tasks[0].model == "gemini-2.5-flash"


def test_assign_models_code_execution():
    # 2. assign_models sets model=None for CODE_EXECUTION task
    task = TaskSpec(
        id="task_2",
        task_type=TaskType.CODE_EXECUTION,
        intent="Run code",
        depends_on=[],
    )
    goal = GoalSpec(
        raw_goal="Run some code",
        intent="running",
        tasks=[task],
        success_criteria="Code ran",
    )
    
    result = assign_models(goal)
    assert result.tasks[0].model is None


def test_assign_models_returns_same_object():
    # 3. assign_models returns the same GoalSpec object (in-place mutation)
    task = TaskSpec(
        id="task_3",
        task_type=TaskType.FORMATTING,
        intent="Format output",
        depends_on=[],
    )
    goal = GoalSpec(
        raw_goal="Format output",
        intent="formatting",
        tasks=[task],
        success_criteria="Output formatted",
    )
    
    result = assign_models(goal)
    assert result is goal


def test_task_model_map_has_5_keys():
    # 4. TASK_MODEL_MAP has 5 keys matching TaskType values
    assert len(DEFAULT_TASK_MODEL_MAP) == 5
    for task_type in TaskType:
        assert task_type.value in DEFAULT_TASK_MODEL_MAP


def test_custom_map_via_monkeypatch(monkeypatch):
    # 5. Custom map via CONTEXT_CONFIG override (monkeypatch TASK_MODEL_MAP directly)
    custom_map = {
        "data_retrieval": "custom-model-1",
        "reasoning": "custom-model-2",
        "formatting": "custom-model-3",
        "code_execution": "custom-model-4",
        "api_call": "custom-model-5",
    }
    
    monkeypatch.setattr(core.model_conductor, "TASK_MODEL_MAP", custom_map)
    
    task = TaskSpec(
        id="task_4",
        task_type=TaskType.DATA_RETRIEVAL,
        intent="Fetch data",
        depends_on=[],
    )
    goal = GoalSpec(
        raw_goal="Fetch data",
        intent="fetching",
        tasks=[task],
        success_criteria="Data fetched",
    )
    
    result = assign_models(goal)
    assert result.tasks[0].model == "custom-model-1"