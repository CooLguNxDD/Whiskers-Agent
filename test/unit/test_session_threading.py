import pytest
import uuid
from langgraph.checkpoint.memory import MemorySaver
from core_graph.graph import build_dynamic_graph
from langchain_core.messages import HumanMessage
from core_graph.mcp_tool import _thread_config

class FakeResponse:
    def __init__(self, content: str):
        self.content = content

class FakeLLM:
    async def ainvoke(self, messages, *args, **kwargs):
        return FakeResponse('{"mode": "chat", "reply": "ok"}')

def test_thread_config():
    config_s1 = _thread_config("s1")
    assert config_s1 == {"configurable": {"thread_id": "s1"}, "recursion_limit": 200}

    config_none = _thread_config(None)
    assert "configurable" in config_none
    assert "thread_id" in config_none["configurable"]
    thread_id = config_none["configurable"]["thread_id"]
    assert isinstance(thread_id, str)
    assert len(thread_id) > len("ephemeral-")
    assert thread_id.startswith("ephemeral-")
    assert config_none["recursion_limit"] == 200

def test_thread_config_sets_recursion_limit():
    config = _thread_config("s")
    assert config["recursion_limit"] == 200
    assert config["configurable"]["thread_id"] == "s"

@pytest.mark.asyncio
async def test_stream_consumer_surfaces_error(monkeypatch):
    # Ensure env DATABASE_URL is set/mocked so the early guards don't short-circuit
    monkeypatch.setattr("core_graph.mcp_tool._DB_AVAILABLE", True)
    monkeypatch.setattr("core_graph.mcp_tool._LLM_USABLE", True)

    class FakeAsyncIterator:
        def __init__(self, raises_exc=False):
            self.raises_exc = raises_exc

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.raises_exc:
                raise RuntimeError("boom")
            raise StopAsyncIteration

    class FakeStreamObject:
        def __init__(self):
            self.values = FakeAsyncIterator(raises_exc=True)
            self.messages = FakeAsyncIterator(raises_exc=False)

    class FakeGraph:
        async def astream_events(self, *args, **kwargs):
            return FakeStreamObject()

    fake_graph = FakeGraph()

    async def mock_get_graph():
        return fake_graph

    monkeypatch.setattr("core_graph.mcp_tool._get_graph", mock_get_graph)

    from core_graph.mcp_tool import stream_graph_impl

    yielded_items = []
    async for item in stream_graph_impl("hi"):
        yielded_items.append(item)

    assert any(item.get("type") == "error" for item in yielded_items)
    err_item = next(item for item in yielded_items if item.get("type") == "error")
    assert err_item.get("message") == "boom"

@pytest.mark.asyncio
async def test_build_dynamic_graph_no_checkpointer():
    graph = build_dynamic_graph(
        FakeLLM(),
        context_params={"project_id": 1, "workspace_id": 1},
        api_url="",
        route_registry=None,
        checkpointer=None
    )
    assert graph is not None

    initial_state = {
        "user_query": "hi",
        "candidates": [],
        "plan": [],
        "current_step_index": 0,
        "step_results": [],
        "parallel_groups": [],
        "selected": None,
        "confidence": 0.0,
        "gate_decision": "execute",
        "clarification_question": None,
        "payload": None,
        "response": None,
        "retry_count": 0,
        "force_execute": False,
        "messages": [HumanMessage(content="hi")],
    }
    result = await graph.ainvoke(initial_state)
    assert result is not None
    assert result["triage_mode"] == "chat"

@pytest.mark.asyncio
async def test_session_cross_turn_memory_and_isolation():
    memory = MemorySaver()
    graph = build_dynamic_graph(
        FakeLLM(),
        context_params={"project_id": 1, "workspace_id": 1},
        api_url="",
        route_registry=None,
        checkpointer=memory
    )

    config_t1 = {"configurable": {"thread_id": "t1"}}
    input_1 = {
        "user_query": "hi",
        "messages": [HumanMessage(content="hi")],
        "force_execute": False,
    }
    result_1 = await graph.ainvoke(input_1, config=config_t1)

    input_2 = {
        "user_query": "again",
        "messages": [HumanMessage(content="again")],
        "force_execute": False,
    }
    result_2 = await graph.ainvoke(input_2, config=config_t1)

    assert len(result_2["messages"]) > len(result_1["messages"])

    config_t2 = {"configurable": {"thread_id": "t2"}}
    input_3 = {
        "user_query": "isolated",
        "messages": [HumanMessage(content="isolated")],
        "force_execute": False,
    }
    result_3 = await graph.ainvoke(input_3, config=config_t2)

    assert len(result_3["messages"]) < len(result_2["messages"])
