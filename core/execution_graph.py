"""
Execution graph for goal-oriented MCP pipeline.
Translates GoalSpec DAGs into compiled LangGraphs.
"""

import asyncio
import logging
import operator
from collections import deque
from typing import Annotated, Any, Callable, Coroutine, TypedDict

from langgraph.graph import StateGraph, START, END

from core.goal_spec import GoalSpec, TaskSpec

logger = logging.getLogger("whiskers")


class GoalExecutionState(TypedDict):
    """
    State representation for goal execution.
    """
    goal: GoalSpec
    results: Annotated[dict[str, Any], operator.ior]
    error: str | None


def _topological_sort(tasks: list[TaskSpec]) -> list[TaskSpec]:
    """Sort tasks topologically using Kahn's algorithm."""
    in_degree = {task.id: 0 for task in tasks}
    adj = {task.id: [] for task in tasks}
    task_map = {task.id: task for task in tasks}

    for task in tasks:
        for dep in task.depends_on:
            if dep not in task_map:
                raise ValueError(f"Task {task.id} depends on unknown task {dep}")
            adj[dep].append(task.id)
            in_degree[task.id] += 1

    queue = deque([t_id for t_id, deg in in_degree.items() if deg == 0])
    sorted_tasks = []

    while queue:
        curr_id = queue.popleft()
        sorted_tasks.append(task_map[curr_id])

        for neighbor in adj[curr_id]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(sorted_tasks) != len(tasks):
        raise ValueError("Cycle detected in task dependencies")

    return sorted_tasks


def _make_task_node(task: TaskSpec) -> Callable[[GoalExecutionState], Coroutine[Any, Any, GoalExecutionState]]:
    """Create a LangGraph node function for a specific task."""

    async def node_fn(state: GoalExecutionState) -> GoalExecutionState:
        """Asynchronously execute a single node task in the state graph."""
        logger.debug(f"ExecutionGraph: executing task {task.id} ({task.task_type.value})")
        try:
            # Placeholder execution
            result = {
                "status": "ok",
                "task_id": task.id,
                "intent": task.intent,
            }

            # Return state delta for operator.ior reducer
            return {"results": {task.id: result}}
        except asyncio.CancelledError:
            logger.warning(f"ExecutionGraph: task {task.id} cancelled")
            raise
        except Exception as e:
            logger.exception(f"ExecutionGraph error in task {task.id}: {e}")
            return {"error": "internal_error"}

    return node_fn


def build_goal_graph(goal_spec: GoalSpec):
    """Compile a LangGraph from a GoalSpec."""
    sorted_tasks = _topological_sort(goal_spec.tasks)

    builder = StateGraph(GoalExecutionState)

    for task in sorted_tasks:
        builder.add_node(task.id, _make_task_node(task))

    has_dependents = set()
    for task in sorted_tasks:
        for dep in task.depends_on:
            has_dependents.add(dep)

    for task in sorted_tasks:
        if not task.depends_on:
            builder.add_edge(START, task.id)
        else:
            for dep in task.depends_on:
                builder.add_edge(dep, task.id)

        if task.id not in has_dependents:
            builder.add_edge(task.id, END)

    graph = builder.compile()
    logger.info(f"ExecutionGraph: built graph with {len(goal_spec.tasks)} tasks")
    return graph
