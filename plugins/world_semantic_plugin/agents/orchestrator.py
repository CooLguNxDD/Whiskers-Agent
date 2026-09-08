"""Minimal task decomposition table (step 5). Full GOAP deferred."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CraftStage:
    """One ordered craft phase (name + optional precondition stage + description)."""

    name: str
    precondition: str | None
    description: str


# Precondition chain: terrain → structures → population
STAGES: list[CraftStage] = [
    CraftStage("terrain_carved", None, "Ensure ground / carve volume ready"),
    CraftStage("structures_placed", "terrain_carved", "Place buildings / props"),
    CraftStage("population_spawned", "structures_placed", "Spawn NPCs / fauna"),
]


def decompose(task: str) -> list[dict]:
    """Return ordered craft stages for a free-text task."""
    t = (task or "").lower()
    stages = list(STAGES)
    # Lightweight task filtering: skip population if task is pure placement of props
    if "village" in t or "town" in t or "settlement" in t:
        selected = stages
    elif "camp" in t or "structure" in t or "place" in t or "spawn" in t:
        selected = [s for s in stages if s.name != "population_spawned"]
    else:
        selected = stages

    out = []
    for s in selected:
        out.append(
            {
                "stage": s.name,
                "precondition": s.precondition,
                "description": s.description,
                "task_hint": task,
            }
        )
    return out


def next_ready(completed: set[str], plan: list[dict] | None = None) -> dict | None:
    """Return the next stage whose precondition is satisfied, or None if done."""
    plan = plan or [s.__dict__ for s in STAGES]
    for step in plan:
        name = step["stage"] if isinstance(step, dict) else step.name
        pre = step.get("precondition") if isinstance(step, dict) else step.precondition
        if name in completed:
            continue
        if pre is None or pre in completed:
            return step if isinstance(step, dict) else {
                "stage": step.name,
                "precondition": step.precondition,
                "description": step.description,
            }
    return None
