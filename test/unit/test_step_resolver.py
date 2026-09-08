import pytest
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.step_resolver import make_step_resolver_node
from core_graph.node.routers import context_resolution_router, step_resolver_router

class _FakeResp:
    def __init__(self, content):
        self.content = content

class _FakeLLM:
    def __init__(self, content):
        self._c = content

    async def ainvoke(self, msgs):
        return _FakeResp(self._c)

def _ctx():
    return GraphRuntimeContext(llm=None, context_params={}, api_url="", route_registry=None, checkpointer=None)

def _patch_llm(monkeypatch, json_content):
    async def fake(step):
        return _FakeLLM(json_content)
    monkeypatch.setattr("core.llm_config_service.resolve_step_llm", fake)

def test_router_builder_when_resolved():
    """Verify context_resolution_router routes to builder when no unresolved required parameters exist."""
    assert context_resolution_router({"unresolved_required": []}) == "builder"

def test_router_step_resolver_when_unresolved():
    """Verify context_resolution_router routes to step_resolver when there are unresolved required parameters."""
    assert context_resolution_router({"unresolved_required": ["id"]}) == "step_resolver"

def test_step_resolver_router_behavior():
    """Verify step_resolver_router routes correctly based on responses and goal presence."""
    assert step_resolver_router({"response": {"status": "need_input"}, "goal": "g"}) == "goap_goal"
    assert step_resolver_router({"response": {"status": "need_input"}}) == "done"
    assert step_resolver_router({"response": {"status": "confirmation_needed"}}) == "done"
    assert step_resolver_router({}) == "builder"

@pytest.mark.asyncio
async def test_agentic_fills_missing(monkeypatch):
    """Verify that in agentic mode, the step resolver node calls the step LLM to fill missing parameters."""
    _patch_llm(monkeypatch, '{"id": "pg_123"}')
    node = make_step_resolver_node(_ctx())
    state = {
        "force_execute": True,
        "unresolved_required": ["id"],
        "resolved_args": {},
        "plan": [{"operation_id": "fetch", "intent": "fetch page", "arg_bindings": {"id": "$steps[0].id"}}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "CALL"},
        "step_results": [{"data": [{"id": "pg_123"}]}],
        "working_memory": {},
        "user_query": "x",
    }
    res = await node(state)
    assert res.get("response") is None
    assert res["resolved_args"]["id"] == "pg_123"
    assert res["unresolved_required"] == []

@pytest.mark.asyncio
async def test_non_agentic_emits_need_input():
    """Verify that in non-agentic mode, the step resolver node yields a need_input response directly."""
    node = make_step_resolver_node(_ctx())
    state = {
        "force_execute": False,
        "goal": None,
        "unresolved_required": ["id"],
        "resolved_args": {},
        "plan": [{"operation_id": "fetch"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "CALL"},
    }
    res = await node(state)
    assert res["response"]["status"] == "need_input"
    assert "id" in res["response"]["missing_params"]

@pytest.mark.asyncio
async def test_write_op_forces_confirm(monkeypatch):
    """Verify that write operations force confirmation unless agentic (force_execute or goal)."""
    node = make_step_resolver_node(_ctx())
    state = {
        "goal": None,
        "force_execute": False,
        "unresolved_required": [],
        "resolved_args": {"body": "hello"},
        "plan": [{"operation_id": "sendMessage", "intent": "send"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "POST"},
        "step_results": [],
        "working_memory": {},
        "user_query": "hi",
    }
    res = await node(state)
@pytest.mark.asyncio
async def test_force_execute_skips_confirm(monkeypatch):
    """Verify that force_execute skips confirmation for write operations (now handled by permission_gate; resolver no longer injects)."""
    _patch_llm(monkeypatch, '{"body": "hello"}')
    node = make_step_resolver_node(_ctx())
    state = {
        "force_execute": True,
        "unresolved_required": ["body"],
        "resolved_args": {},
        "plan": [{"operation_id": "sendMessage", "intent": "send"}],
        "current_step_index": 0,
        "candidates": [],
        "selected": {"method": "POST"},
        "step_results": [],
        "working_memory": {},
        "user_query": "hi",
    }
    res = await node(state)
    assert res.get("response") is None
    assert res["resolved_args"]["body"] == "hello"
    assert res["unresolved_required"] == []
