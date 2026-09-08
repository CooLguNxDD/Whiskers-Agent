import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4
from datetime import datetime, timezone

from db_layer.execution_store import create_execution, finalize_execution, list_executions, get_execution
from db_layer.models import WorkflowExecution
from core_graph.node.summary import make_summary_node, make_goal_check_node
from core_graph.node.executor import make_step_dispatcher_node
from core_graph.node.context import GraphRuntimeContext
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

@pytest.fixture
def mock_session():
    with patch("db_layer.execution_store.get_async_session") as mock_get_session:
        mock_session_instance = AsyncMock()
        mock_session_instance.add = MagicMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session_instance
        yield mock_session_instance

@pytest.mark.asyncio
async def test_execution_store_create_finalize(mock_session):
    def fake_add(obj):
        obj.id = uuid4()
    mock_session.add.side_effect = fake_add

    # 1. Test create_execution
    exec_id = await create_execution(
        session_id="test-session",
        workflow_plan_id=str(uuid4()),
        user_query="test query",
        iteration=1
    )
    assert exec_id is not None
    mock_session.add.assert_called_once()
    mock_session.flush.assert_called_once()
    mock_session.commit.assert_called_once()

    # 2. Test finalize_execution
    mock_session.commit.reset_mock()
    await finalize_execution(
        exec_id=exec_id,
        step_results=[{"res": "ok"}],
        working_memory={"key": "val"},
        summary="Test summary",
        content="Test content",
        carry={"param": "value"},
        status="done"
    )
    mock_session.execute.assert_called_once()
    mock_session.commit.assert_called_once()

@pytest.mark.asyncio
async def test_list_executions(mock_session):
    exec_obj = MagicMock()
    exec_obj.id = uuid4()
    exec_obj.session_id = "s-123"
    exec_obj.workflow_plan_id = uuid4()
    exec_obj.user_query = "q"
    exec_obj.iteration = 0
    exec_obj.step_results = []
    exec_obj.working_memory = {}
    exec_obj.summary = "sum"
    exec_obj.content = "cont"
    exec_obj.carry = {}
    exec_obj.status = "done"
    exec_obj.created_at = datetime.now(timezone.utc)
    exec_obj.updated_at = datetime.now(timezone.utc)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [exec_obj]
    mock_session.execute.return_value = mock_result

    res = await list_executions("s-123")
    assert len(res) == 1
    assert res[0]["session_id"] == "s-123"
    assert res[0]["summary"] == "sum"

@pytest.mark.asyncio
async def test_summary_node_structured_json():
    # Mock LLM returning valid JSON
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AIMessage(
        content='{"summary": "Test recap", "content": "fetched 123", "carry": {"record_id": 123}}'
    )

    ctx = GraphRuntimeContext(
        llm=fake_llm,
        context_params={},
        api_url="",
        route_registry=None
    )

    summary_node = make_summary_node(ctx)

    state = {
        "user_query": "find record",
        "step_results": [{"id": 123}],
        "working_memory": {"old": "data"},
        "execution_id": str(uuid4()),
        "response": {"existing": True}
    }

    with patch("db_layer.execution_store.finalize_execution", new_callable=AsyncMock) as mock_finalize:
        res = await summary_node(state)
        
        assert res["summary"] == "Test recap"
        assert res["working_memory"] == {"old": "data", "record_id": 123}
        assert res["response"]["summary"] == "Test recap"
        assert res["response"]["content"] == "fetched 123"
        assert res["response"]["carry"] == {"record_id": 123}
        
        mock_finalize.assert_called_once()
        args, kwargs = mock_finalize.call_args
        assert kwargs["summary"] == "Test recap"
        assert kwargs["content"] == "fetched 123"
        assert kwargs["carry"] == {"record_id": 123}
        assert kwargs["status"] == "done"


@pytest.mark.asyncio
async def test_summary_node_folds_layout_into_carry():
    """Last emit_layout-style step result is folded into response.carry.layout."""
    from core_graph.node.summary import _extract_layout_from_step_results

    full = {
        "version": 1,
        "meta": {"audience": "default", "generatedAt": "t"},
        "blocks": [],
    }
    assert _extract_layout_from_step_results([
        {"status": "ok", "layout": full},
    ]) == full
    hero_layout = {
        **full,
        "blocks": [{"type": "hero", "id": "h", "props": {"name": "a", "tagline": "b"}}],
    }
    assert _extract_layout_from_step_results([
        {"response": {"layout": hero_layout}},
    ]) == hero_layout
    # Bare layout parked as the step result (no "layout" wrapper key).
    assert _extract_layout_from_step_results([full]) == full
    assert _extract_layout_from_step_results([{"id": 1}]) is None

    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AIMessage(
        content='{"summary": "Rendered layout", "content": "", "carry": {"note": "x"}}'
    )
    ctx = GraphRuntimeContext(
        llm=fake_llm, context_params={}, api_url="", route_registry=None
    )
    summary_node = make_summary_node(ctx)
    layout = {"version": 1, "meta": {"audience": "default", "generatedAt": "t"}, "blocks": [{"type": "hero"}]}
    state = {
        "user_query": "show SRE work",
        "step_results": [{"status": "ok", "layout": layout}],
        "working_memory": {},
        "execution_id": str(uuid4()),
        "response": {},
    }
    with patch("db_layer.execution_store.finalize_execution", new_callable=AsyncMock):
        res = await summary_node(state)
    assert res["response"]["carry"]["layout"] == layout
    assert res["response"]["layout"] == layout
    assert res["response"]["carry"]["note"] == "x"
    assert res["working_memory"]["layout"] == layout

@pytest.mark.asyncio
async def test_summary_node_malformed_llm_fallback():
    # Mock LLM returning plain text instead of JSON
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AIMessage(
        content="This is just plain text summary."
    )

    ctx = GraphRuntimeContext(
        llm=fake_llm,
        context_params={},
        api_url="",
        route_registry=None
    )

    summary_node = make_summary_node(ctx)

    state = {
        "user_query": "find record",
        "step_results": [],
        "working_memory": {},
        "execution_id": str(uuid4()),
        "response": {}
    }

    with patch("db_layer.execution_store.finalize_execution", new_callable=AsyncMock) as mock_finalize:
        res = await summary_node(state)
        
        assert res["summary"] == "This is just plain text summary."
        assert res["working_memory"] == {}
        assert res["response"]["summary"] == "This is just plain text summary."
        assert res["response"]["content"] == ""
        assert res["response"]["carry"] == {}
        
        mock_finalize.assert_called_once()


@pytest.mark.asyncio
async def test_summary_node_rejects_json_dump_as_summary():
    """LLM echoing step-result / envelope JSON must not become chat summary text."""
    envelope_dump = json.dumps(
        {
            "status": "ok",
            "steps_executed": 2,
            "data": [
                {
                    "status": "ok",
                    "data": {
                        "_meta": {
                            "tool": "list_sessions",
                            "item_count": 21,
                            "shaped": True,
                        }
                    },
                }
            ],
        }
    )
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AIMessage(content=envelope_dump)

    ctx = GraphRuntimeContext(
        llm=fake_llm,
        context_params={},
        api_url="",
        route_registry=None,
    )
    summary_node = make_summary_node(ctx)
    state = {
        "user_query": "fetch the first Jules session",
        "step_results": [{"status": "ok"}, {"status": "ok"}],
        "working_memory": {},
        "execution_id": str(uuid4()),
        "response": {"status": "ok", "steps_executed": 2},
    }

    with patch("db_layer.execution_store.finalize_execution", new_callable=AsyncMock):
        res = await summary_node(state)

    assert res["summary"] == "Completed 2 steps successfully."
    assert "steps_executed" not in res["summary"]
    assert res["response"]["summary"] == "Completed 2 steps successfully."
    assert res["response"]["message"] == "Completed 2 steps successfully."

@pytest.mark.asyncio
async def test_summary_node_truncates_large_results():
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AIMessage(
        content='{"summary": "recap", "content": "", "carry": {}}'
    )
    ctx = GraphRuntimeContext(
        llm=fake_llm,
        context_params={},
        api_url="",
        route_registry=None
    )
    summary_node = make_summary_node(ctx)

    large_step = {"data": "a" * 17000}
    state = {
        "user_query": "query",
        "step_results": [large_step],
        "working_memory": {},
        "execution_id": str(uuid4()),
        "response": {}
    }

    with patch("db_layer.execution_store.finalize_execution", new_callable=AsyncMock) as mock_finalize:
        await summary_node(state)

        called_messages = fake_llm.ainvoke.call_args[0][0]
        human_msg = called_messages[1]
        assert isinstance(human_msg, HumanMessage)

        assert "Step results:\n" in human_msg.content
        step_results_content = human_msg.content.split("Step results:\n")[1]
        assert len(step_results_content) <= 16000

@pytest.mark.asyncio
async def test_initial_state_session_id_injection(monkeypatch):
    monkeypatch.setattr("core_graph.mcp_tool._DB_AVAILABLE", True)
    monkeypatch.setattr("core_graph.mcp_tool._LLM_USABLE", True)

    class FakeGraph:
        async def ainvoke(self, state, config):
            assert state["session_id"] == "session-456"
            assert state["execution_id"] is None
            return {"response": {"status": "ok"}}

    fake_graph = FakeGraph()
    monkeypatch.setattr("core_graph.mcp_tool._get_graph", AsyncMock(return_value=fake_graph))

    from core_graph.mcp_tool import run_graph_impl

    # headless path
    await run_graph_impl("hi", session_id="session-456")

@pytest.mark.asyncio
async def test_step_dispatcher_persists_and_sets_execution_id():
    ctx = GraphRuntimeContext(
        llm=AsyncMock(),
        context_params={},
        api_url="",
        route_registry=None
    )

    step_dispatcher_node = make_step_dispatcher_node(ctx)

    # State with all steps completed (current_step_index + 1 >= len(plan))
    state = {
        "plan": [{"operation_id": "test"}],
        "current_step_index": 0,
        "step_results": [],
        "response": {"result": "ok"},
        "user_query": "query",
        "session_id": "s-123",
        "workflow_plan_id": str(uuid4()),
        "execution_id": None,
        "iterations": 0
    }

    with patch("db_layer.execution_store.create_execution", new_callable=AsyncMock) as mock_create, \
         patch("db_layer.execution_store.finalize_execution", new_callable=AsyncMock) as mock_finalize:
        mock_create.return_value = "exec-789"
        
        res = await step_dispatcher_node(state)
        
        assert res["execution_id"] == "exec-789"
        mock_create.assert_called_once()
        mock_finalize.assert_called_once()

@pytest.mark.asyncio
async def test_goal_check_node_continue_clears_execution_id():
    fake_llm = AsyncMock()
    # Mock LLM goal check deciding "continue"
    fake_llm.ainvoke.return_value = AIMessage(
        content='{"done": false, "reason": "need more", "next_hint": "search again"}'
    )

    ctx = GraphRuntimeContext(
        llm=fake_llm,
        context_params={},
        api_url="",
        route_registry=None
    )

    goal_check_node = make_goal_check_node(ctx)

    state = {
        "iterations": 0,
        "goal": "overarching goal",
        "max_iterations": 5,
        "execution_id": "exec-abc",
        "step_results": [],
        "working_memory": {}
    }

    res = await goal_check_node(state)
    assert res["goal_loop_decision"] == "continue"
    assert res["execution_id"] is None
