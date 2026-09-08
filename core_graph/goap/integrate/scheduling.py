import re
from typing import Iterable

from core_graph.goap.integrate.fanout import _strip_op_prefix

# Ordered: first matching category wins. str.startswith(tuple) runs in C.
_VERB_CATEGORIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("search", "list", "find", "get", "read", "fetch", "query", "lookup"), "data_retrieval"),
    (("summarize", "summary", "analyze", "analyse", "evaluate", "assess", "reason", "plan", "classify", "decide"), "reasoning"),
    (("create", "update", "insert", "write", "render", "format", "compose", "draft", "build", "generate"), "formatting"),
)


def _classify_step_task(step: dict) -> str:
    """Classify the plan step's task type based on its operation_id verb.

    Returns one of "data_retrieval" | "reasoning" | "formatting" by matching verbs
    within the stripped, lowercased operation_id. Defaults to "data_retrieval".
    """
    op_id = step.get("operation_id") or ""
    stripped = _strip_op_prefix(op_id).lower()
    # Token-based match (split on non-alphanumeric) to avoid substring collisions
    # like "thread" containing "read". A token matches a verb if it equals or
    # starts with that verb (e.g. "lists" -> "list").
    tokens = [t for t in re.split(r"[^a-z0-9]+", stripped) if t]

    for verbs, label in _VERB_CATEGORIES:
        if any(tok.startswith(verbs) for tok in tokens):
            return label
    return "data_retrieval"


def assign_step_models(
    steps: list[dict],
    pool_entries: list[dict] | None,
    policy: dict | None = None,
) -> None:
    """Stamp step['model'] (in place) for steps lacking one, from the active chat pool.

    Applies a policy-aware assignment strategy (off, strength, explicit, or task_type)
    to map steps to active models, falling back to complexity-based selection on mismatch.
    """
    active = [e for e in (pool_entries or []) if e.get("is_active")]
    if not active:
        return

    strongest = max(active, key=lambda e: e.get("strength", 0.0))
    weakest = min(active, key=lambda e: e.get("strength", 0.0))
    by_name = {e["name"]: e for e in active if "name" in e}

    strategy = "strength"
    if isinstance(policy, dict):
        strategy = policy.get("strategy", "strength")

    def strength_pick(st: dict) -> str | None:
        """Pick a model from the active pool based on the step's complexity."""
        # Select model based on complexity (complex gets strongest, simple gets weakest)
        complex_step = bool(st.get("arg_bindings")) or st.get("depends_on") is not None
        return (strongest if complex_step else weakest).get("name")

    for step in steps or []:
        if step.get("model"):
            continue

        if strategy == "off":
            continue
        elif strategy == "explicit":
            overrides = policy.get("op_overrides") if isinstance(policy, dict) else None
            name = overrides.get(step.get("operation_id")) if isinstance(overrides, dict) else None
            if name in by_name:
                step["model"] = name
            else:
                step["model"] = strength_pick(step)
        elif strategy == "task_type":
            tt = _classify_step_task(step)
            tt_map = policy.get("task_type_map") if isinstance(policy, dict) else None
            name = tt_map.get(tt) if isinstance(tt_map, dict) else None
            if name in by_name:
                step["model"] = name
            else:
                step["model"] = strength_pick(step)
        else:
            step["model"] = strength_pick(step)


def derive_parallel_groups(steps: list[dict]) -> list[list[int]]:
    """
    Derive parallel execution groups from step dependencies.

    Groups steps by level-based topological ordering, preserving execution order,
    and ensuring isolatable (fan-out or wait) steps run individually.
    """
    if not steps:
        return []

    n_steps = len(steps)
    deps_map = {i: set() for i in range(n_steps)}

    # Scan values recursively to find step dependencies like $steps[N]
    def _scan_for_step_deps(val: any, step_idx: int, deps_set: set[int]) -> None:
        if isinstance(val, str):
            for match in re.findall(r"\$steps\[(\d+)\]", val):
                j = int(match)
                if 0 <= j < step_idx:
                    deps_set.add(j)
        elif isinstance(val, (list, tuple, set)):
            for item in val:
                _scan_for_step_deps(item, step_idx, deps_set)
        elif isinstance(val, dict):
            for item in val.values():
                _scan_for_step_deps(item, step_idx, deps_set)

    # Compute predecessors from depends_on and arg_bindings
    for i, step in enumerate(steps):
        depends_on = step.get("depends_on")
        if depends_on is not None:
            if isinstance(depends_on, int):
                if 0 <= depends_on < i:
                    deps_map[i].add(depends_on)
            elif isinstance(depends_on, (list, tuple, set)) or (
                isinstance(depends_on, Iterable) and not isinstance(depends_on, str)
            ):
                for d in depends_on:
                    if isinstance(d, int):
                        if 0 <= d < i:
                            deps_map[i].add(d)

        arg_bindings = step.get("arg_bindings")
        if isinstance(arg_bindings, dict):
            for val in arg_bindings.values():
                _scan_for_step_deps(val, i, deps_map[i])

    # Mark isolatable steps (fan-out steps and wait steps)
    isolatable = [False] * n_steps
    for i, step in enumerate(steps):
        is_fan_out = bool(step.get("for_each")) or bool(step.get("for_each_values"))
        is_wait = step.get("kind") == "wait"
        isolatable[i] = is_fan_out or is_wait

    # Calculate levels using topological order (ascending pass)
    levels = [0] * n_steps
    for i in range(n_steps):
        step_deps = deps_map[i]
        if step_deps:
            levels[i] = 1 + max(levels[d] for d in step_deps)
        else:
            levels[i] = 0

    # Build parallel execution groups by level
    max_level = max(levels)
    groups = []
    for L in range(max_level + 1):
        # Append non-isolatable group at this level
        non_iso = [i for i in range(n_steps) if levels[i] == L and not isolatable[i]]
        if non_iso:
            groups.append(non_iso)
        # Append isolatable singletons at this level
        iso = [i for i in range(n_steps) if levels[i] == L and isolatable[i]]
        for i in iso:
            groups.append([i])

    return groups
