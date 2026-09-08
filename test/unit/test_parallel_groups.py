"""
Unit tests for the parallel groups derivation algorithm.
"""

import pytest
from core_graph.goap.integrate import derive_parallel_groups


def check_invariants(steps: list[dict], groups: list[list[int]]):
    """
    Assert structural invariants of derived parallel groups.
    1. Every index 0..len-1 must appear exactly once.
    2. For any group with len > 1, no two steps in the group have a dependency relation.
    """
    n_steps = len(steps)
    flat = []
    for g in groups:
        flat.extend(g)
    assert set(flat) == set(range(n_steps)), f"Flattened groups {flat} must contain all indices exactly once"
    assert len(flat) == n_steps, f"Duplicate indices found in groups {groups}"

    # Build direct dependency graph
    import re
    deps_map = {i: set() for i in range(n_steps)}

    def _scan(val, step_idx, s):
        if isinstance(val, str):
            for match in re.findall(r"\$steps\[(\d+)\]", val):
                s.add(int(match))
        elif isinstance(val, (list, tuple, set)):
            for item in val:
                _scan(item, step_idx, s)
        elif isinstance(val, dict):
            for item in val.values():
                _scan(item, step_idx, s)

    for i, step in enumerate(steps):
        dep = step.get("depends_on")
        if dep is not None:
            if isinstance(dep, int):
                deps_map[i].add(dep)
            elif isinstance(dep, (list, tuple, set)):
                for d in dep:
                    deps_map[i].add(d)
        bindings = step.get("arg_bindings")
        if isinstance(bindings, dict):
            for val in bindings.values():
                _scan(val, i, deps_map[i])

    # Transitively close the reachability graph
    reachable = {i: set(deps_map[i]) for i in range(n_steps)}
    for i in range(n_steps):
        for d in list(reachable[i]):
            reachable[i].update(reachable[d])

    # Assert no group has steps with dependency relationships
    for group in groups:
        for x in group:
            for y in group:
                if x != y:
                    assert y not in reachable[x], f"Step {x} depends on {y} in the same parallel group"
                    assert x not in reachable[y], f"Step {y} depends on {x} in the same parallel group"


def test_empty_steps():
    """
    Cover: empty -> []
    """
    steps = []
    groups = derive_parallel_groups(steps)
    assert groups == []
    check_invariants(steps, groups)


def test_independent_steps():
    """
    Cover: two independent steps (no deps) -> single group [[0, 1]].
    """
    steps = [
        {"operation_id": "op0"},
        {"operation_id": "op1"},
    ]
    groups = derive_parallel_groups(steps)
    assert groups == [[0, 1]]
    check_invariants(steps, groups)


def test_independent_subtask_steps_batch_together():
    """
    Cover (decompose-first): steps planned from two independent sub-tasks — distinct
    ops with literal args, no depends_on/$steps refs — land in one parallel batch.
    """
    steps = [
        {"operation_id": "notion_search", "args": {"query": "cat"}},
        {"operation_id": "list_bookings", "args": {"workspace_id": 1}},
    ]
    groups = derive_parallel_groups(steps)
    assert groups == [[0, 1]]
    check_invariants(steps, groups)


def test_linear_chain():
    """
    Cover: linear chain via depends_on: step1 depends_on 0, step2 depends_on 1 -> [[0], [1], [2]].
    """
    steps = [
        {"operation_id": "op0"},
        {"operation_id": "op1", "depends_on": 0},
        {"operation_id": "op2", "depends_on": 1},
    ]
    groups = derive_parallel_groups(steps)
    assert groups == [[0], [1], [2]]
    check_invariants(steps, groups)


def test_diamond():
    """
    Cover: diamond: step0 (none), step1 dep 0, step2 dep 0, step3 deps [1,2] -> [[0], [1, 2], [3]].
    """
    steps = [
        {"operation_id": "op0"},
        {"operation_id": "op1", "depends_on": 0},
        {"operation_id": "op2", "depends_on": 0},
        {"operation_id": "op3", "depends_on": [1, 2]},
    ]
    groups = derive_parallel_groups(steps)
    assert groups == [[0], [1, 2], [3]]
    check_invariants(steps, groups)


def test_arg_bindings_dependency():
    """
    Cover: arg_bindings dependency: step1 has arg_bindings {"id": "$steps[0].id"} -> result [[0], [1]].
    """
    steps = [
        {"operation_id": "op0"},
        {"operation_id": "op1", "arg_bindings": {"id": "$steps[0].id"}},
    ]
    groups = derive_parallel_groups(steps)
    assert groups == [[0], [1]]
    check_invariants(steps, groups)


def test_fan_out_exclusion():
    """
    Cover: fan-out exclusion: step1 has for_each "$steps[0].results" -> emitted as singleton;
    independent step2 -> level 0 group [[0, 2]] then [[1]] (fan-out singleton at level 1).
    """
    steps = [
        {"operation_id": "op0"},
        {"operation_id": "op1", "for_each": "$steps[0].results"},
        {"operation_id": "op2"},
    ]
    groups = derive_parallel_groups(steps)
    assert groups == [[0, 2], [1]]
    check_invariants(steps, groups)


def test_wait_exclusion():
    """
    Cover: wait exclusion: a step with kind=="wait" is always a singleton group.
    """
    steps = [
        {"operation_id": "op0"},
        {"operation_id": "op1", "kind": "wait", "depends_on": 0},
        {"operation_id": "op2"},
    ]
    groups = derive_parallel_groups(steps)
    assert groups == [[0, 2], [1]]
    check_invariants(steps, groups)
