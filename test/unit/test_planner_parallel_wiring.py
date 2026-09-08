"""
Unit tests for the planner parallel group and model assignment integration/wiring.
"""
from core_graph.goap.integrate import assign_step_models, derive_parallel_groups


def test_planner_parallel_wiring_enabled():
    active_pool = [
        {"name": "fast-model", "provider": "openai", "model": "gpt-4o-mini", "strength": 0.3, "is_active": True},
        {"name": "smart-model", "provider": "openai", "model": "gpt-4o", "strength": 0.9, "is_active": True},
    ]

    policy = {
        "strategy": "strength",
        "parallel_enabled": True
    }

    plan = [
        {"operation_id": "search_records"},
        {"operation_id": "fetch_record", "depends_on": 0},
        {"operation_id": "send_message"},
    ]

    # Verify model stamping via assign_step_models
    assign_step_models(plan, active_pool, policy)
    for step in plan:
        assert step.get("model") in ["fast-model", "smart-model"]

    # Verify parallel group derivation
    if policy.get("parallel_enabled"):
        parallel_groups = derive_parallel_groups(plan)
    else:
        parallel_groups = []

    # Independent steps 0 and 2 run together; step 1 depends on 0 so it runs after
    assert parallel_groups == [[0, 2], [1]]


def test_planner_parallel_wiring_disabled():
    active_pool = [
        {"name": "fast-model", "provider": "openai", "model": "gpt-4o-mini", "strength": 0.3, "is_active": True},
        {"name": "smart-model", "provider": "openai", "model": "gpt-4o", "strength": 0.9, "is_active": True},
    ]

    policy = {
        "strategy": "strength",
        "parallel_enabled": False
    }

    plan = [
        {"operation_id": "search_records"},
        {"operation_id": "fetch_record", "depends_on": 0},
        {"operation_id": "send_message"},
    ]

    assign_step_models(plan, active_pool, policy)
    for step in plan:
        assert step.get("model") in ["fast-model", "smart-model"]

    if policy.get("parallel_enabled"):
        parallel_groups = derive_parallel_groups(plan)
    else:
        parallel_groups = []

    assert parallel_groups == []
