"""Unit tests for GoapAgent session store, state codec, and node tool helpers."""

from __future__ import annotations

import asyncio

import pytest

from core_graph.goap_agent.session_store import SessionStore, reset_session_store_for_tests
from core_graph.goap_agent.state_codec import initial_state, merge_delta, to_public_snapshot
from core_graph.goap_agent import node_runner


@pytest.fixture(autouse=True)
def _reset_store():
    reset_session_store_for_tests()
    try:
        from core_graph.goap_agent.mcp_tools import reset_goap_session_locks_for_tests

        reset_goap_session_locks_for_tests()
    except Exception:
        pass
    yield
    reset_session_store_for_tests()
    try:
        from core_graph.goap_agent.mcp_tools import reset_goap_session_locks_for_tests

        reset_goap_session_locks_for_tests()
    except Exception:
        pass


def test_initial_state_seeds_query():
    st = initial_state("hello", force_execute=True, session_id="s1")
    assert st["user_query"] == "hello"
    assert st["force_execute"] is True
    assert st["session_id"] == "s1"
    assert st["plan"] == []
    assert st["working_memory"] == {}


def test_merge_delta_and_snapshot():
    st = initial_state("q")
    st = merge_delta(st, {"triage_mode": "task", "response": None})
    assert st["triage_mode"] == "task"
    snap = to_public_snapshot(st)
    assert snap["triage_mode"] == "task"
    assert "user_query" in snap


def test_snapshot_redacts_messages():
    st = initial_state("q")
    st["messages"] = [{"role": "user", "content": "secret"}]
    snap = to_public_snapshot(st, include_messages=False)
    assert "redacted" in str(snap["messages"]).lower() or snap["messages"] != st["messages"]


@pytest.mark.asyncio
async def test_session_store_crud():
    store = SessionStore(max_sessions=8, ttl_s=600)
    sess = await store.create(initial_state("hi"), session_id="abc")
    assert sess.session_id == "abc"
    got = await store.get("abc")
    assert got is not None
    assert got.state["user_query"] == "hi"
    await store.update("abc", merge_delta(got.state, {"triage_mode": "chat"}), last_node="triage")
    got2 = await store.get("abc")
    assert got2.last_node == "triage"
    assert got2.history == ["triage"]
    assert await store.destroy("abc") is True
    assert await store.get("abc") is None


def test_list_node_names_includes_core_nodes():
    names = node_runner.list_node_names(resolve_when=False)
    assert "turn_init" in names
    assert "triage" in names
    assert "planner" in names
    assert "executor" in names
    assert "goap_goal" in names


@pytest.mark.asyncio
async def test_invoke_node_unknown(monkeypatch):
    """invoke_node's KeyError branch, isolated from the real node map build.

    node_runner caches a module-global GraphRuntimeContext/node map on first
    use, which constructs a real LLM client (get_graph_core_llm). Whichever
    test runs first in the session pays that cost and needs live provider
    credentials — this test only cares about the missing-node lookup, so it
    stubs get_node_map instead of depending on a real AI connection.
    """
    async def _fake_node_map(*, force_reload: bool = False):
        return {"turn_init": lambda state: state}

    monkeypatch.setattr(node_runner, "get_node_map", _fake_node_map)
    with pytest.raises(KeyError):
        await node_runner.invoke_node("not_a_real_node", initial_state("x"))


@pytest.mark.asyncio
async def test_create_session_tool_logic():
    """Exercise create/get/invoke path with a mock node map (no real LLM)."""
    from core_graph.goap_agent.session_store import get_session_store
    from core_graph.goap_agent.state_codec import merge_delta, to_public_snapshot

    store = await get_session_store()
    state = initial_state("find contacts", force_execute=True)
    sess = await store.create(state)
    # Simulate turn_init delta
    delta = {"response": None, "working_memory": {}}
    new_state = merge_delta(sess.state, delta)
    await store.update(sess.session_id, new_state, last_node="turn_init")
    got = await store.get(sess.session_id)
    assert got.last_node == "turn_init"
    snap = to_public_snapshot(got.state)
    assert snap["user_query"] == "find contacts"


@pytest.mark.asyncio
async def test_concurrent_submit_confirm_and_clarify_no_lost_update():
    """Per-session lock: concurrent confirm + clarify both apply."""
    from core_graph.goap_agent.mcp_tools import (
        GoapAgent_submit_clarify,
        GoapAgent_submit_confirm,
    )
    from core_graph.goap_agent.session_store import get_session_store

    store = await get_session_store()
    sess = await store.create(initial_state("base query"), session_id="lock-race-1")

    r1, r2 = await asyncio.gather(
        GoapAgent_submit_confirm(session_id=sess.session_id, approved=True),
        GoapAgent_submit_clarify(session_id=sess.session_id, answer="use contact list"),
    )
    assert r1["status"] == "ok"
    assert r2["status"] == "ok"

    got = await store.get(sess.session_id)
    assert got is not None
    # Both mutations must be present (order may vary; neither may wipe the other).
    assert got.state.get("force_execute") is True
    assert got.state.get("gate_decision") == "execute"
    assert "use contact list" in (got.state.get("user_query") or "")
    assert "clarification" in (got.state.get("user_query") or "").lower()
    # History should include both last_node stamps
    assert "submit_confirm" in got.history
    assert "submit_clarify" in got.history
