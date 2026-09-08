"""
Model conductor utilities.
"""
import logging
from typing import Dict, Optional

from core.goal_spec import GoalSpec, TaskType
from utils.server_config import CONTEXT_CONFIG

logger = logging.getLogger("whiskers")

DEFAULT_TASK_MODEL_MAP: dict[str, str | None] = {
    "data_retrieval": "gemini-2.5-flash",
    "reasoning": "claude-sonnet-4-20250514",
    "formatting": "gemini-2.5-flash",
    "code_execution": None,
    "api_call": None,
}

TASK_MODEL_MAP: dict[str, str | None] = {}

def _load_task_model_map() -> dict[str, str | None]:
    """Loads the task model map from CONTEXT_CONFIG or falls back to DEFAULT_TASK_MODEL_MAP."""
    return CONTEXT_CONFIG.get("goal_models", DEFAULT_TASK_MODEL_MAP)

TASK_MODEL_MAP = _load_task_model_map()

def assign_models(goal_spec: GoalSpec) -> GoalSpec:
    """Assigns LLM models to each task in the GoalSpec based on TaskType.

    Args:
        goal_spec: The GoalSpec object to mutate.

    Returns:
        The mutated GoalSpec object.
    """
    for task in goal_spec.tasks:
        model = TASK_MODEL_MAP.get(task.task_type.value)
        task.model = model
        logger.debug(
            f"ModelConductor: assigned model {model} to task {task.id} ({task.task_type.value})"
        )

    return goal_spec