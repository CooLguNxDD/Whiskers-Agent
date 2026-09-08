"""
MTU-4 acceptance tests — wait_node + builder_router + record matching.

Ground truth for:
- builder_router: wait steps -> "wait_node", normal -> "executor", builder error -> "executor".
- _find_record: locate the target record across list / {data:[]} / {results:[]} shapes.
- wait_node: polls until until-condition met (success), aborts on fail_on, times out;
  uses a tiny patched poll executor + zero sleep so the test is fast.
"""

import asyncio
import pytest

from core_graph.node.routers import builder_router
from core_graph.node import wait as wait_mod


# --------------------------- builder_router ---------------------------

def test_builder_router_wait_step():
    state = {
        "plan": [{"operation_id": "get_get_export_requests", "kind": "wait"}],
        "current_step_index": 0,
    }
    assert builder_router(state) == "wait_node"


def test_builder_router_normal_step():
    state = {
        "plan": [{"operation_id": "list_records"}],
        "current_step_index": 0,
    }
    assert builder_router(state) == "executor"


def test_builder_router_builder_error_passes_through():
    # builder emitted a need_input/error response -> let executor pass it through,
    # never the wait_node, even if the step is a wait.
    state = {
        "plan": [{"operation_id": "get_get_export_requests", "kind": "wait"}],
        "current_step_index": 0,
        "response": {"status": "need_input", "message": "x"},
    }
    assert builder_router(state) == "executor"


# --------------------------- _find_record ---------------------------

def test_find_record_in_list():
    resp = [{"id": 1, "status": "PENDING"}, {"id": 42, "status": "COMPLETED"}]
    rec = wait_mod._find_record(resp, {"id": 42})
    assert rec and rec["status"] == "COMPLETED"


def test_find_record_in_data_envelope():
    resp = {"status": "ok", "data": [{"id": 7, "status": "PROCESSING"}]}
    rec = wait_mod._find_record(resp, {"id": 7})
    assert rec and rec["status"] == "PROCESSING"


def test_find_record_in_results_envelope():
    resp = {"results": [{"id": 9, "status": "COMPLETED"}]}
    rec = wait_mod._find_record(resp, {"id": 9})
    assert rec and rec["id"] == 9


def test_find_record_missing_returns_none():
    resp = [{"id": 1}, {"id": 2}]
    assert wait_mod._find_record(resp, {"id": 999}) is None


# --------------------------- wait_node loop ---------------------------

class _Ctx:
    route_registry = None
    llm = None
    context_params = {}
    api_url = ""


def _wait_step(**over):
    step = {
        "operation_id": "get_get_export_requests",
        "plugin_id": "report_plugin",
        "kind": "wait",
        "match": {"id": "$steps[0].id"},
        "until": {"field": "status", "equals": "COMPLETED"},
        "fail_on": ["FAILED", "EXPIRED"],
        "interval_s": 0,   # no real sleep in tests
        "max_polls": 5,
    }
    step.update(over)
    return step


def _state(step):
    return {
        "plan": [{"operation_id": "post_add_export_request"}, step],
        "current_step_index": 1,
        "step_results": [{"id": 42, "status": "PENDING"}],  # create step result
    }


def _run(node, state):
    # Fresh loop per call so the helper is safe when the wider suite (pytest-asyncio)
    # also manages an event loop.
    return asyncio.run(node(state))


def test_wait_node_succeeds_when_completed(monkeypatch):
    # poll returns PENDING twice, then COMPLETED.
    seq = iter([
        {"response": [{"id": 42, "status": "PENDING"}]},
        {"response": [{"id": 42, "status": "PROCESSING"}]},
        {"response": [{"id": 42, "status": "COMPLETED", "assetLink": "x.csv"}]},
    ])

    async def fake_exec(step, step_results, state, *a, **k):
        return next(seq)

    monkeypatch.setattr(wait_mod, "_execute_single", fake_exec)
    node = wait_mod.make_wait_node(_Ctx())
    out = _run(node, _state(_wait_step()))
    resp = out["response"]
    assert isinstance(resp, dict)
    assert resp.get("status") == "COMPLETED"
    assert resp.get("id") == 42


def test_wait_node_aborts_on_fail_status(monkeypatch):
    async def fake_exec(step, step_results, state, *a, **k):
        return {"response": [{"id": 42, "status": "FAILED"}]}

    monkeypatch.setattr(wait_mod, "_execute_single", fake_exec)
    node = wait_mod.make_wait_node(_Ctx())
    out = _run(node, _state(_wait_step()))
    assert out["response"]["status"] == "error"


def test_wait_node_times_out(monkeypatch):
    async def fake_exec(step, step_results, state, *a, **k):
        return {"response": [{"id": 42, "status": "PENDING"}]}

    monkeypatch.setattr(wait_mod, "_execute_single", fake_exec)
    node = wait_mod.make_wait_node(_Ctx())
    out = _run(node, _state(_wait_step(max_polls=3)))
    assert out["response"]["status"] == "error"
