import pytest
from core.goal_spec import GoalSpec, TaskSpec, TaskType
from core.execution_graph import _topological_sort, build_goal_graph, GoalExecutionState


def test_topological_sort_single():
    t1 = TaskSpec(id="task_0", task_type=TaskType.DATA_RETRIEVAL, intent="test", depends_on=[])
    sorted_tasks = _topological_sort([t1])
    assert sorted_tasks == [t1]


def test_topological_sort_two_tasks():
    t1 = TaskSpec(id="task_0", task_type=TaskType.DATA_RETRIEVAL, intent="test1", depends_on=[])
    t2 = TaskSpec(id="task_1", task_type=TaskType.REASONING, intent="test2", depends_on=["task_0"])
    
    # Should sort task_0 then task_1 regardless of input order
    sorted_tasks = _topological_sort([t2, t1])
    assert sorted_tasks == [t1, t2]


def test_topological_sort_cycle():
    t1 = TaskSpec(id="task_0", task_type=TaskType.DATA_RETRIEVAL, intent="test1", depends_on=["task_1"])
    t2 = TaskSpec(id="task_1", task_type=TaskType.REASONING, intent="test2", depends_on=["task_0"])
    
    with pytest.raises(ValueError, match="Cycle detected"):
        _topological_sort([t1, t2])


def test_build_goal_graph_compiles():
    t1 = TaskSpec(id="task_0", task_type=TaskType.DATA_RETRIEVAL, intent="test", depends_on=[])
    goal = GoalSpec(raw_goal="test", intent="test", tasks=[t1], success_criteria="test")
    graph = build_goal_graph(goal)
    assert graph is not None


@pytest.mark.asyncio
async def test_graph_invoke_single_task():
    t1 = TaskSpec(id="task_0", task_type=TaskType.DATA_RETRIEVAL, intent="test", depends_on=[])
    goal = GoalSpec(raw_goal="test", intent="test", tasks=[t1], success_criteria="test")
    graph = build_goal_graph(goal)
    
    initial_state = {"goal": goal, "results": {}, "error": None}
    final_state = await graph.ainvoke(initial_state)
    
    assert "results" in final_state
    assert "task_0" in final_state["results"]
    assert final_state["results"]["task_0"]["status"] == "ok"
    assert final_state["results"]["task_0"]["intent"] == "test"


@pytest.mark.asyncio
async def test_graph_invoke_two_tasks():
    t1 = TaskSpec(id="task_0", task_type=TaskType.DATA_RETRIEVAL, intent="test1", depends_on=[])
    t2 = TaskSpec(id="task_1", task_type=TaskType.REASONING, intent="test2", depends_on=["task_0"])
    goal = GoalSpec(raw_goal="test", intent="test", tasks=[t1, t2], success_criteria="test")
    graph = build_goal_graph(goal)
    
    initial_state = {"goal": goal, "results": {}, "error": None}
    final_state = await graph.ainvoke(initial_state)
    
    assert "results" in final_state
    assert "task_0" in final_state["results"]
    assert "task_1" in final_state["results"]
    assert final_state["results"]["task_1"]["status"] == "ok"
    assert final_state["results"]["task_1"]["intent"] == "test2"
