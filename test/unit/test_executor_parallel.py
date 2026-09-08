"""Unit tests for executor parallel gather partial-failure behavior."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def test_parallel_gather_collects_all_results():
    """asyncio.gather(return_exceptions=True) returns ALL results even if some raise.

    This is the primitive the fixed step_dispatcher_node relies on. The test
    documents that gather does NOT stop at first exception — it's the caller's
    duty to NOT early-return on the first Exception it sees in the results list.
    """
    async def _run():
        async def ok(v):
            return v
        async def fail():
            raise ValueError("boom")

        results = await asyncio.gather(ok(1), fail(), ok(3), return_exceptions=True)
        # All 3 positions populated
        assert len(results) == 3
        assert results[0] == 1
        assert isinstance(results[1], ValueError)
        assert results[2] == 3

    asyncio.run(_run())


def test_parallel_gather_partial_result_collection():
    """Simulate the fixed step_dispatcher_node partial-failure loop.

    Verify: successes are stored, failures collected, result list has correct length.
    """
    import asyncio

    async def _run():
        async def step(i):
            if i == 1:
                raise RuntimeError(f"step {i} failed")
            return {"response": {"status": "ok", "data": i}}

        current_group = [0, 1, 2]
        parallel_results = await asyncio.gather(
            *[step(i) for i in current_group], return_exceptions=True
        )

        results = []
        errors = []
        for i, res in zip(current_group, parallel_results):
            while len(results) <= i:
                results.append({})
            if isinstance(res, Exception):
                results[i] = {"error": repr(res)}
                errors.append({"index": i, "error": repr(res)})
            else:
                results[i] = res

        # All 3 positions should be populated
        assert len(results) == 3
        # Successful results preserved
        assert results[0] == {"response": {"status": "ok", "data": 0}}
        assert results[2] == {"response": {"status": "ok", "data": 2}}
        # Failed step recorded with type via repr (not silently dropped)
        assert results[1] == {"error": "RuntimeError('step 1 failed')"}
        assert errors == [{"index": 1, "error": "RuntimeError('step 1 failed')"}]

    asyncio.run(_run())


def test_more_steps_router_partial_with_goal_recovers():
    """partial + goal must recover (goap_goal), not bare END."""
    from core_graph.node.routers import more_steps_router

    state = {
        "plan": [{"operation_id": "a"}, {"operation_id": "b"}],
        "current_step_index": 1,
        "goal": "get first jules session",
        "response": {
            "status": "partial",
            "failed_steps": [{"index": 0, "error": "404"}],
            "completed_steps": [{}, {"status": "ok"}],
        },
    }
    assert more_steps_router(state) == "recover"


def test_more_steps_router_partial_without_goal_done():
    from core_graph.node.routers import more_steps_router

    state = {
        "plan": [{"operation_id": "a"}],
        "current_step_index": 0,
        "response": {"status": "partial", "failed_steps": []},
    }
    assert more_steps_router(state) == "done"


def test_soft_failure_detection():
    from core_graph.node.executor import _is_soft_failure

    assert _is_soft_failure({"status": "error", "message": "404"}) is True
    assert _is_soft_failure({"status": "ok", "data": {}}) is False
    assert _is_soft_failure({"error": "boom"}) is True
    assert _is_soft_failure({"response": {"status": "error"}}) is True


@pytest.mark.asyncio
async def test_step_dispatcher_fans_remaining_of_group_starting_at_zero():
    """After sequential step 0 of group [0,1], remaining peer 1 must still run."""
    from core_graph.node.executor import make_step_dispatcher_node
    from core_graph.node.context import GraphRuntimeContext

    ctx = MagicMock(spec=GraphRuntimeContext)
    ctx.route_registry = MagicMock()
    ctx.llm = MagicMock()
    ctx.context_params = {}
    ctx.api_url = "http://test"

    calls = []

    async def fake_execute(step, results, state, *a, **kw):
        calls.append(step["operation_id"])
        if step["operation_id"] == "op_b":
            return {"status": "ok", "data": "from-b"}
        return {"status": "ok", "data": "from-a"}

    dispatcher = make_step_dispatcher_node(ctx)
    state = {
        "current_step_index": 0,
        "step_results": [],
        "response": {"status": "error", "message": "get failed", "http_status": 404},
        "plan": [
            {"operation_id": "op_a", "plugin_id": "p"},
            {"operation_id": "op_b", "plugin_id": "p"},
        ],
        "parallel_groups": [[0, 1]],
        "candidates": [],
    }

    with patch("core_graph.node.executor._execute_step", side_effect=fake_execute):
        out = await dispatcher(state)

    # Peer op_b must have been executed even though step 0 soft-failed
    assert "op_b" in calls
    assert out["response"]["status"] == "partial"
    assert len(out["step_results"]) >= 2
    # Sibling success preserved
    assert out["step_results"][1].get("status") == "ok" or out["step_results"][1].get("data") == "from-b"

