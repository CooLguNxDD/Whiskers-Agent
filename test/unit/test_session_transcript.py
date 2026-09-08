"""Session transcript: user-facing replies land in `messages`, internals stay hidden.

Regression for chat→classic follow-ups ("search up!" after a named entity was
resolved in chat): without an AIMessage on the checkpoint, the planner's
context block only saw HumanMessages and treated the follow-up literally.
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from core_graph.node.context import GraphRuntimeContext
from core_graph.node.summary import make_summary_node
from core_graph.node.triage import (
    _MAX_TRANSCRIPT_MESSAGES,
    _trim_transcript,
    make_chat_node,
    make_turn_init_node,
)
from core_graph.prompts.context_block import format_context_block, history_from_messages


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _StubLLM:
    def __init__(self, content="{}"):
        self._c = content
        self.last_msgs = None

    async def ainvoke(self, msgs):
        self.last_msgs = msgs
        return _FakeResp(self._c)


def _ctx(llm):
    return GraphRuntimeContext(
        llm=llm, context_params={}, api_url="", route_registry=None, checkpointer=None
    )


@pytest.fixture(autouse=True)
def _harness_memory_off(monkeypatch):
    import utils.server_config as sc
    monkeypatch.setattr(
        sc, "TRIAGE_CONFIG", {"harness_memory_enabled": False, "harness_memory_top_k": 3}
    )


def test_history_from_messages_maps_roles_and_filters_internal():
    msgs = [
        HumanMessage(content="who is mika"),
        AIMessage(content="Which Mika?"),
        HumanMessage(content="mika misono?"),
        AIMessage(content="Blue Archive character.", additional_kwargs={"internal": True}),
        AIMessage(content="Mika Misono is a Tea Party member."),
    ]
    hist = history_from_messages(msgs)
    assert hist == [
        {"role": "user", "content": "who is mika"},
        {"role": "assistant", "content": "Which Mika?"},
        {"role": "user", "content": "mika misono?"},
        {"role": "assistant", "content": "Mika Misono is a Tea Party member."},
    ]
    with_internal = history_from_messages(msgs, include_internal=True)
    assert any("Blue Archive" in h["content"] for h in with_internal)
    assert len(with_internal) == 5


def test_format_context_block_uses_user_assistant_labels():
    hist = history_from_messages(
        [HumanMessage(content="who is mika"), AIMessage(content="Which Mika?")]
    )
    block = format_context_block(hist)
    assert "- user: who is mika" in block
    assert "- assistant: Which Mika?" in block
    assert "HumanMessage" not in block
    assert "AIMessage" not in block


@pytest.mark.asyncio
async def test_chat_node_returns_user_facing_aimessage():
    reply = "Mika Misono is a Blue Archive character."
    node = make_chat_node(_ctx(_StubLLM(reply)))
    out = await node({"user_query": "mika misono?"})
    assert out["response"]["message"] == reply
    msgs = out["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], AIMessage)
    assert msgs[0].content == reply
    assert not (msgs[0].additional_kwargs or {}).get("internal")


@pytest.mark.asyncio
async def test_summary_node_returns_user_facing_aimessage():
    payload = json.dumps(
        {
            "summary": "Mika Misono is a Tea Party member at Trinity.",
            "content": {},
            "carry": {"mika_name": "Misono Mika"},
        }
    )
    node = make_summary_node(_ctx(_StubLLM(payload)))
    out = await node(
        {
            "user_query": "search up!",
            "original_query": "search up!",
            "working_memory": {},
            "step_results": [{"operation_id": "web_search", "status": "ok"}],
            "plan": [],
        }
    )
    assert "Mika Misono" in out["summary"]
    msgs = out["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], AIMessage)
    assert msgs[0].content == out["summary"]
    assert not (msgs[0].additional_kwargs or {}).get("internal")


@pytest.mark.asyncio
async def test_turn_init_emits_remove_message_for_stale_transcript():
    node = make_turn_init_node(_ctx(_StubLLM()))
    keep = _MAX_TRANSCRIPT_MESSAGES
    messages = [
        HumanMessage(content=f"turn {i}", id=f"id-{i}") for i in range(keep + 5)
    ]
    # One extra without an id must be skipped, not crash.
    messages[0] = HumanMessage(content="no-id")
    out = await node({"user_query": "next", "messages": messages, "working_memory": {}})
    removals = out["messages"]
    assert all(isinstance(m, RemoveMessage) for m in removals)
    removed_ids = {m.id for m in removals}
    assert "id-0" not in removed_ids  # skipped — no id on that object
    # Stale window is the first 5; four of those have ids.
    assert "id-1" in removed_ids
    assert f"id-{keep + 4}" not in removed_ids
    assert out["original_query"] == "next"


def test_trim_transcript_noop_when_under_cap():
    messages = [HumanMessage(content="hi", id="a"), AIMessage(content="yo", id="b")]
    assert _trim_transcript(messages) == []


def test_resolve_search_query_uses_prior_user_topic_not_imperative():
    from core_graph.goap.integrate import is_bare_search_followup, resolve_search_query

    assert is_bare_search_followup("search up!")
    assert is_bare_search_followup("look it up")
    assert is_bare_search_followup("google that")
    assert is_bare_search_followup("search up please")
    assert is_bare_search_followup("search that up for me")
    assert not is_bare_search_followup("search up Mika Misono")
    assert not is_bare_search_followup("who is mika misono?")

    hist = [
        {"role": "user", "content": "who is mika misono?"},
        {"role": "assistant", "content": "Mika Misono is a Blue Archive character."},
        {"role": "user", "content": "search up!"},
    ]
    assert resolve_search_query("search up!", history=hist) == "who is mika misono?"
    assert resolve_search_query("search for Mika Misono") == "search for Mika Misono"

    from core_graph.goap.integrate import apply_followup_search_topic

    repaired = apply_followup_search_topic(
        {"query": "search up"}, "search up!", history=hist
    )
    assert repaired["query"] == "who is mika misono?"
    injected = apply_followup_search_topic({}, "search up!", history=hist)
    assert injected["query"] == "who is mika misono?"
    kept = apply_followup_search_topic(
        {"query": "Mika Misono Blue Archive"}, "search up!", history=hist
    )
    assert kept["query"] == "Mika Misono Blue Archive"
