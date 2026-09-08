"""Unit tests for the decompose node (core_graph/node/decompose.py) — the
decompose-first pipeline pass that splits the user request into sub-tasks
before candidate embedding retrieval."""

import json

import pytest

import core_graph.node.decompose as decompose_mod
from core_graph.node.decompose import make_decompose_node

# Captured before the autouse fixture swaps it out, so the real fetcher can be restored.
_REAL_GET_TOOLS_SUMMARY = decompose_mod._get_tools_summary
from core_graph.node.context import GraphRuntimeContext
from utils.server_config import MAX_SUBTASKS


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #

class _FakeResp:
    def __init__(self, content):
        self.content = content


class _StubLLM:
    """Returns canned content and records the messages it was invoked with."""

    def __init__(self, content="{}", exc=None):
        self._c = content
        self._exc = exc
        self.last_msgs = None

    async def ainvoke(self, msgs):
        self.last_msgs = msgs
        if self._exc:
            raise self._exc
        return _FakeResp(self._c)


def _ctx(llm):
    return GraphRuntimeContext(llm=llm, context_params={}, api_url="", route_registry=None, checkpointer=None)


@pytest.fixture(autouse=True)
def _no_tools_summary(monkeypatch):
    """Isolate tests from the DB-backed tools summary and its module cache."""
    monkeypatch.setattr(decompose_mod, "_tools_summary_cache", None)

    async def _fake_summary():
        return "Live tool catalog: 1. [CALL] searchRecords ..."
    monkeypatch.setattr(decompose_mod, "_get_tools_summary", _fake_summary)


def _payload(sub_tasks, intent="Do things", seeds=None):
    return json.dumps({
        "intent": intent,
        "sub_tasks": sub_tasks,
        "seed_values": seeds or {},
        "confidence": 1.0,
    })


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_decompose_parses_sub_tasks_and_seed_values():
    llm = _StubLLM(_payload(["search projects for 'Alpha'", "post a status update"], seeds={"query": "Alpha"}))
    node = make_decompose_node(_ctx(llm))
    out = await node({"user_query": "Search for Alpha and post an update"})
    assert out["sub_tasks"] == ["search projects for 'Alpha'", "post a status update"]
    assert out["decompose_intent"] == "Do things"
    assert out["decompose_seed_values"] == {"query": "Alpha"}
    assert out["messages"]
    assert out["messages"][0].additional_kwargs.get("internal") is True


@pytest.mark.asyncio
async def test_decompose_caps_at_max_subtasks():
    many = [f"task {i}" for i in range(MAX_SUBTASKS + 3)]
    llm = _StubLLM(_payload(many))
    node = make_decompose_node(_ctx(llm))
    out = await node({"user_query": "do many things"})
    assert len(out["sub_tasks"]) == MAX_SUBTASKS
    assert out["sub_tasks"] == many[:MAX_SUBTASKS]


@pytest.mark.asyncio
async def test_decompose_strips_and_drops_non_string_entries():
    llm = _StubLLM(json.dumps({"intent": "x", "sub_tasks": ["  a  ", "", 42, "b"], "seed_values": {}}))
    node = make_decompose_node(_ctx(llm))
    out = await node({"user_query": "q"})
    assert out["sub_tasks"] == ["a", "b"]


# --------------------------------------------------------------------------- #
# Graceful degrade — never gate/halt here
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_decompose_llm_exception_falls_back_to_user_query():
    llm = _StubLLM(exc=RuntimeError("model down"))
    node = make_decompose_node(_ctx(llm))
    out = await node({"user_query": "find contact John"})
    assert out["sub_tasks"] == ["find contact John"]
    assert out["decompose_seed_values"] == {}
    assert "response" not in out


@pytest.mark.asyncio
async def test_decompose_bad_json_falls_back_to_user_query():
    llm = _StubLLM("not json at all")
    node = make_decompose_node(_ctx(llm))
    out = await node({"user_query": "find contact John"})
    assert out["sub_tasks"] == ["find contact John"]


@pytest.mark.asyncio
async def test_decompose_empty_sub_tasks_falls_back_to_user_query():
    llm = _StubLLM(_payload([]))
    node = make_decompose_node(_ctx(llm))
    out = await node({"user_query": "find contact John"})
    assert out["sub_tasks"] == ["find contact John"]


@pytest.mark.asyncio
async def test_decompose_tools_summary_failure_tolerated(monkeypatch):
    """build_tools_summary raising must not break decomposition."""
    monkeypatch.setattr(decompose_mod, "_tools_summary_cache", None)

    async def _boom(limit=40):
        raise RuntimeError("db down")
    import db_layer.gateway_settings_store as gss
    monkeypatch.setattr(gss, "build_tools_summary", _boom)
    # restore the real cached fetcher (autouse fixture stubbed it)
    monkeypatch.setattr(decompose_mod, "_get_tools_summary", _REAL_GET_TOOLS_SUMMARY)

    llm = _StubLLM(_payload(["list appointments"]))
    node = make_decompose_node(_ctx(llm))
    out = await node({"user_query": "list my appointments"})
    assert out["sub_tasks"] == ["list appointments"]


# --------------------------------------------------------------------------- #
# Replan context threading
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_decompose_threads_replan_context_and_focus():
    llm = _StubLLM(_payload(["retry the search with broader terms"]))
    node = make_decompose_node(_ctx(llm))
    out = await node({
        "user_query": "search again",
        "replan_context": [{"attempt": 1, "operation_id": "searchRecords", "args": {"q": "x"}, "outcome": "empty", "detail": "search returned 0 results"}],
        "remaining_goal_facts": ["did:searchRecords"],
    })
    assert out["sub_tasks"] == ["retry the search with broader terms"]
    human = llm.last_msgs[-1].content
    assert "Previous failed attempts" in human
    assert "did:searchRecords" in human
    assert "search again" in human


@pytest.mark.asyncio
async def test_decompose_includes_catalog_block():
    llm = _StubLLM(_payload(["list appointments"]))
    node = make_decompose_node(_ctx(llm))
    await node({"user_query": "list my appointments"})
    human = llm.last_msgs[-1].content
    assert "Tool catalog overview" in human
