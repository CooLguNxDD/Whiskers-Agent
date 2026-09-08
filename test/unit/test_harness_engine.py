"""Unit tests for core harness (static instructions, format, recipe soft-apply)."""

from unittest.mock import AsyncMock, patch

import pytest

from core_graph.harness.engine import (
    HarnessContext,
    format_harness_block,
    load_static_instructions,
    soft_apply_recipe_to_plan,
)
from core_graph.harness.recipe_apply import (
    apply_recipe_order,
    build_skeleton_from_recipe,
    pick_best_recipe,
    recipe_ops_available,
)


def test_load_static_instructions_includes_collection_by_id():
    """Core collection→by-id instruction file is loaded (disk seed fallback)."""
    text = load_static_instructions(force_reload=True)
    assert "list" in text.lower() or "List" in text
    assert "id" in text.lower()


@pytest.mark.asyncio
async def test_load_instructions_async_uses_db_formatter():
    """Async loader prefers core.memory format_instructions_text_async."""
    from core_graph.harness.engine import load_instructions_async

    with patch(
        "core.memory.format_instructions_text_async",
        new_callable=AsyncMock,
        return_value="Always list before get by id.",
    ):
        text = await load_instructions_async()
    assert "list before get" in text.lower()


def test_format_harness_block_caps_and_sections():
    """Harness block includes core + recipes + anti sections and truncates."""
    block = format_harness_block(
        static_instructions="Always list before get.",
        recipes=[
            {
                "op_ids": ["juleslist_sessions", "julesget_session"],
                "content": "Goal: first session\nPlan: list → get",
                "similarity": 0.91,
            }
        ],
        anti_patterns=[
            {
                "plan_ops": ["julesget_session", "juleslist_sessions"],
                "detail": "missing sessionId",
                "content": "bad order",
            }
        ],
        max_chars=5000,
    )
    assert "Core instructions" in block
    assert "Learned recipes" in block
    assert "juleslist_sessions" in block
    assert "Avoid" in block
    assert "julesget_session" in block


def test_recipe_ops_available():
    cands = [
        {"operation_id": "a"},
        {"operation_id": "b"},
    ]
    assert recipe_ops_available(["a", "b"], cands)
    assert not recipe_ops_available(["a", "c"], cands)
    assert not recipe_ops_available([], cands)


def test_pick_best_recipe_threshold_and_availability():
    cands = [
        {"operation_id": "juleslist_sessions"},
        {"operation_id": "julesget_session"},
        {"operation_id": "other"},
    ]
    recipes = [
        {
            "op_ids": ["julesget_session", "juleslist_sessions"],
            "similarity": 0.5,
        },
        {
            "op_ids": ["juleslist_sessions", "julesget_session"],
            "similarity": 0.9,
        },
        {
            "op_ids": ["missing_op"],
            "similarity": 0.99,
        },
    ]
    best = pick_best_recipe(recipes, cands, similarity_threshold=0.78)
    assert best is not None
    assert best["op_ids"][0] == "juleslist_sessions"


def test_apply_recipe_order_reorders_get_before_list():
    """Recipe soft-apply puts list before get."""
    candidates = [
        {
            "operation_id": "jules_plugin__juleslist_sessions",
            "plugin_id": "jules_plugin",
            "method": "CALL",
            "parameters": {"type": "object", "properties": {}},
            "description": "List",
        },
        {
            "operation_id": "jules_plugin__julesget_session",
            "plugin_id": "jules_plugin",
            "method": "CALL",
            "parameters": {
                "type": "object",
                "properties": {"sessionId": {"type": "string"}},
                "required": ["sessionId"],
            },
            "description": "Get",
        },
    ]
    broken = [
        {
            "operation_id": "jules_plugin__julesget_session",
            "plugin_id": "jules_plugin",
            "args": {},
            "arg_bindings": {},
        },
        {
            "operation_id": "jules_plugin__juleslist_sessions",
            "plugin_id": "jules_plugin",
            "args": {},
            "arg_bindings": {},
        },
    ]
    recipe_ops = [
        "jules_plugin__juleslist_sessions",
        "jules_plugin__julesget_session",
    ]
    fixed = apply_recipe_order(broken, recipe_ops, candidates)
    assert fixed[0]["operation_id"] == "jules_plugin__juleslist_sessions"
    assert fixed[1]["operation_id"] == "jules_plugin__julesget_session"


def test_build_skeleton_from_recipe():
    candidates = [
        {
            "operation_id": "jules_plugin__juleslist_sessions",
            "plugin_id": "jules_plugin",
            "method": "CALL",
            "parameters": {"type": "object", "properties": {}},
            "description": "List",
            "is_fast_path": True,
        },
        {
            "operation_id": "jules_plugin__julesget_session",
            "plugin_id": "jules_plugin",
            "method": "CALL",
            "parameters": {
                "type": "object",
                "properties": {"sessionId": {"type": "string"}},
                "required": ["sessionId"],
            },
            "description": "Get",
            "is_fast_path": True,
        },
    ]
    steps = build_skeleton_from_recipe(
        [
            "jules_plugin__juleslist_sessions",
            "jules_plugin__julesget_session",
        ],
        candidates,
    )
    assert len(steps) == 2
    assert steps[0]["operation_id"].endswith("juleslist_sessions")


def test_soft_apply_recipe_to_plan_uses_best_recipe():
    cands = [
        {
            "operation_id": "list_op",
            "plugin_id": "p",
            "method": "CALL",
            "parameters": {},
        },
        {
            "operation_id": "get_op",
            "plugin_id": "p",
            "method": "CALL",
            "parameters": {
                "type": "object",
                "properties": {"sessionId": {"type": "string"}},
                "required": ["sessionId"],
            },
        },
    ]
    plan = [
        {"operation_id": "get_op", "args": {}, "arg_bindings": {}},
        {"operation_id": "list_op", "args": {}, "arg_bindings": {}},
    ]
    harness = HarnessContext(
        enabled=True,
        best_recipe={
            "op_ids": ["list_op", "get_op"],
            "similarity": 0.95,
        },
    )
    out = soft_apply_recipe_to_plan(plan, harness, cands)
    assert out[0]["operation_id"] == "list_op"


@pytest.mark.asyncio
async def test_record_success_recipe_calls_save():
    from core_graph.harness.episode import record_success_recipe

    state = {
        "user_query": "first Jules session",
        "plan": [
            {"operation_id": "juleslist_sessions", "plugin_id": "jules_plugin"},
            {"operation_id": "julesget_session", "plugin_id": "jules_plugin"},
        ],
        "summary": "ok",
        "execution_id": "exec-test-1",
    }
    with patch(
        "core.memory.save_plan_recipe",
        new_callable=AsyncMock,
        return_value={"status": "ok", "id": 1},
    ) as mock_save:
        # Force config enabled
        with patch(
            "core_graph.harness.episode._harness_cfg",
            return_value={"enabled": True, "write_recipes": True},
        ):
            res = await record_success_recipe(state, tenant_id=1)
        assert res is not None
        assert res["status"] == "ok"
        mock_save.assert_called_once()
        kwargs = mock_save.call_args.kwargs
        assert kwargs["op_ids"] == ["juleslist_sessions", "julesget_session"]


@pytest.mark.asyncio
async def test_retrieve_harness_static_when_rag_fails():
    from core_graph.harness.engine import retrieve_harness

    with patch(
        "core.memory.search_plan_recipes",
        new_callable=AsyncMock,
        side_effect=RuntimeError("no embed"),
    ), patch(
        "core.memory.search_anti_patterns",
        new_callable=AsyncMock,
        side_effect=RuntimeError("no embed"),
    ), patch(
        "core_graph.harness.engine._harness_cfg",
        return_value={
            "enabled": True,
            "recipe_top_k": 3,
            "anti_top_k": 3,
            "similarity_threshold": 0.78,
            "max_block_chars": 3500,
        },
    ):
        ctx = await retrieve_harness("fetch first Jules session", [], tenant_id=1)
    assert ctx.enabled
    assert "list" in ctx.static_instructions.lower() or "List" in ctx.static_instructions
    assert ctx.block  # static still formats
