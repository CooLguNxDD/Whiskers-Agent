"""
Goal and task specifications for the goal-oriented MCP pipeline.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging

# Set up logging using the project logger convention
logger = logging.getLogger("whiskers")


class TaskType(str, Enum):
    """Supported task types in the goal-oriented MCP pipeline."""

    DATA_RETRIEVAL = "data_retrieval"
    REASONING = "reasoning"
    CODE_EXECUTION = "code_execution"
    FORMATTING = "formatting"
    API_CALL = "api_call"


@dataclass
class TaskSpec:
    """Specification of an individual task step in a goal's execution plan.

    Attributes:
        id: Unique identifier for the task.
        task_type: The type of task to perform.
        intent: Description of what the task is trying to achieve.
        depends_on: List of other TaskSpec IDs that must be completed first.
        model: Optional LLM model identifier assigned by the ModelConductor.
        tools: List of MCP tool names allowed/suggested by the SemanticRegistry.
    """

    id: str
    task_type: TaskType
    intent: str
    depends_on: list[str]
    model: str | None = None
    tools: list[str] = field(default_factory=list)


@dataclass
class GoalSpec:
    """Specification of a high-level goal and the tasks required to satisfy it.

    Attributes:
        raw_goal: The original natural language prompt or query.
        intent: The parsed intent of the overall goal.
        tasks: Ordered list of TaskSpec objects forming a directed acyclic graph.
        success_criteria: Description of what constitutes successful completion.
        context: Metadata and execution context dictionary.
    """

    raw_goal: str
    intent: str
    tasks: list[TaskSpec]
    success_criteria: str
    context: dict = field(default_factory=dict)
