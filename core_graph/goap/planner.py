"""
GOAP planner implementing A* search over WorldState transitions.
"""

import heapq
from dataclasses import dataclass
from typing import Iterable, Optional
from core_graph.goap.world_state import WorldState
from core_graph.goap.action import GoapAction
from utils.server_config import GOAP_MAX_STEPS, GOAP_MAX_EXPANSIONS


@dataclass
class GoapPlan:
    """
    Represents a planned sequence of actions to achieve a goal fact set.
    """
    actions: list[GoapAction]
    steps: list[dict]
    cost: float


def _compile_steps(actions: list[GoapAction], initial_world: WorldState) -> list[dict]:
    """
    Compile execution steps from a sequence of GoapActions, deriving arg_bindings.
    """
    steps = []
    for i, action in enumerate(actions):
        arg_bindings = {}
        depends_on_indices = []

        # Process preconditions to establish argument bindings and dependencies
        for prec in action.preconditions:
            if not prec.startswith("have:"):
                continue
            param = prec[len("have:"):]

            # Seed facts from the initial world state do not require bindings
            if initial_world.has(prec):
                continue

            # Find the earliest prior step that produces the required fact
            found_j = None
            for j in range(i):
                if prec in actions[j].effects:
                    found_j = j
                    break

            if found_j is not None:
                producer = actions[found_j]
                field = producer.effect_field_map().get(prec, param)
                real_param = action.precondition_param_map().get(prec, param)
                arg_bindings[real_param] = f"$steps[{found_j}].{field}"
                depends_on_indices.append(found_j)

        depends_on = max(depends_on_indices) if depends_on_indices else None
        is_fast_path = False
        if action.candidate and isinstance(action.candidate, dict):
            is_fast_path = bool(action.candidate.get("is_fast_path"))

        steps.append({
            "operation_id": action.operation_id,
            "plugin_id": action.plugin_id,
            "is_fast_path": is_fast_path,
            "intent": action.intent,
            "args": {},
            "arg_bindings": arg_bindings,
            "depends_on": depends_on,
            "model": action.model,
        })

    return steps


def plan(
    goal: Iterable[str],
    world: WorldState,
    actions: list[GoapAction],
    *,
    max_steps: int = GOAP_MAX_STEPS,
    max_expansions: int = GOAP_MAX_EXPANSIONS,
) -> Optional[GoapPlan]:
    """
    Finds a minimal sequence of GoapActions to satisfy the goal preconditions.
    """
    goal_set = set(goal)
    if world.satisfies(goal_set):
        return GoapPlan(actions=[], steps=[], cost=0.0)

    # Priority queue: (f_score, tie_breaker, current_world, path, g_cost, path_set)
    frontier = []
    counter = 0

    # Initial state setup
    h_init = len(world.missing(goal_set))
    heapq.heappush(frontier, (h_init, counter, world, [], 0.0, frozenset()))
    counter += 1

    visited = {world: 0.0}
    expansions = 0

    while frontier:
        f, _, current_world, path, current_g, path_set = heapq.heappop(frontier)
        expansions += 1
        if expansions > max_expansions:
            return None

        # Check if current world state satisfies goal facts
        if current_world.satisfies(goal_set):
            steps = _compile_steps(path, world)
            return GoapPlan(actions=path, steps=steps, cost=current_g)

        # Limit path length to max_steps
        if len(path) >= max_steps:
            continue

        # Try to expand with all available actions in their stable list order
        for action in actions:
            if action in path_set:
                continue

            if not current_world.satisfies(action.preconditions):
                continue

            successor_world = current_world.apply(action.effects)
            next_g = current_g + action.cost

            # Prune if we found a path to this state with lower or equal cost
            if successor_world in visited and visited[successor_world] <= next_g:
                continue

            visited[successor_world] = next_g
            h_next = len(successor_world.missing(goal_set))
            f_next = next_g + h_next

            heapq.heappush(
                frontier,
                (f_next, counter, successor_world, path + [action], next_g, path_set | {action}),
            )
            counter += 1

    return None
