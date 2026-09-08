from typing import Optional

from core_graph.goap.derive import _ID_ALIASES
from core_graph.goap import planner
from core_graph.goap.planner import GoapPlan
from core_graph.goap.derive import derive_actions
from core_graph.goap.world_state import WorldState
from utils.server_config import GOAP_MAX_STEPS, GOAP_MAX_EXPANSIONS


def parse_goal_extraction(parsed: dict | None) -> tuple[list[str], list[str], str]:
    """
    Parse the LLM goal extraction JSON response into goals, seeds, and intent.
    """
    if not parsed or not isinstance(parsed, dict):
        return ([], [], "")

    goal = parsed.get("goal")
    if not isinstance(goal, list):
        goal = []

    seed_facts = parsed.get("seed_facts")
    if not isinstance(seed_facts, list):
        seed_facts = []

    intent = parsed.get("intent")
    if not isinstance(intent, str):
        intent = str(intent) if intent is not None else ""

    return (goal, seed_facts, intent)


def parse_goal_extraction_extended(
    parsed: dict | None
) -> tuple[list[str], list[str], str, float | None, list[dict] | None, dict | None]:
    """
    Parse the LLM goal extraction JSON response into goals, seeds, intent,
    confidence score, clarifying questions, and fan_out descriptors.
    """
    if not parsed or not isinstance(parsed, dict):
        return ([], [], "", None, None, None)

    goal, seed_facts, intent = parse_goal_extraction(parsed)

    confidence = parsed.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (ValueError, TypeError):
            confidence = None

    clarifying_questions = parsed.get("clarifying_questions")
    if clarifying_questions is not None:
        if not isinstance(clarifying_questions, list):
            clarifying_questions = None

    fan_out = parsed.get("fan_out")
    if fan_out is not None and not isinstance(fan_out, dict):
        fan_out = None

    return (goal, seed_facts, intent, confidence, clarifying_questions, fan_out)


def parse_seed_values(parsed: dict | None) -> dict:
    """
    Parse seed_values from the goal response.
    """
    if not parsed or not isinstance(parsed, dict):
        return {}
    seed_values = parsed.get("seed_values")
    if not isinstance(seed_values, dict):
        return {}
    return dict(seed_values)
