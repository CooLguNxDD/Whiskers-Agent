"""Unit tests for the triage node (core_graph/node/triage.py) — context-aware
chat/task classification and contextual chat replies.

Regression coverage for: triage previously classified purely from
`state["user_query"]`, ignoring `working_memory` / `last_summary` / `messages`
that the checkpointer preserves across turns, so a context-dependent follow-up
(e.g. "mika?" after a task about Tea Party members) was misrouted to a
generic, context-free "chat" reply.
"""

import json

import pytest
from langchain_core.messages import HumanMessage

from core_graph.node.triage import make_triage_node, make_chat_node
from core_graph.node.context import GraphRuntimeContext


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _StubLLM:
    """Returns canned content and records the messages it was invoked with."""

    def __init__(self, content="{}"):
        self._c = content
        self.last_msgs = None
        self.calls = 0

    async def ainvoke(self, msgs):
        self.last_msgs = msgs
        self.calls += 1
        return _FakeResp(self._c)


def _ctx(llm):
    return GraphRuntimeContext(llm=llm, context_params={}, api_url="", route_registry=None, checkpointer=None)


def _all_content(msgs):
    return "\n".join(getattr(m, "content", "") for m in msgs)


@pytest.fixture(autouse=True)
def _harness_memory_off(monkeypatch):
    """Default off — matches config default; enabling tested explicitly below."""
    import utils.server_config as sc
    monkeypatch.setattr(sc, "TRIAGE_CONFIG", {"harness_memory_enabled": False, "harness_memory_top_k": 3})


# --------------------------------------------------------------------------- #
# triage_node: context injection
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_triage_no_prior_context_sends_bare_query():
    llm = _StubLLM(json.dumps({"mode": "chat", "reply": "hi"}))
    node = make_triage_node(_ctx(llm))
    await node({"user_query": "hello"})
    # format_context_block always seeds Current time (UTC), so triage injects a
    # context SystemMessage even with no prior turn state (PROMPT + ctx + query).
    assert len(llm.last_msgs) == 3
    assert "Current time (UTC):" in llm.last_msgs[1].content
    assert llm.last_msgs[-1].content == "hello"


@pytest.mark.asyncio
async def test_triage_folds_working_memory_and_last_summary_into_prompt():
    llm = _StubLLM(json.dumps({"mode": "task", "reply": ""}))
    node = make_triage_node(_ctx(llm))
    state = {
        "user_query": "mika?",
        "working_memory": {"tea_party_members": ["Nagisa", "Seia", "Mika"], "school": "Trinity General School"},
        "last_summary": "Tea Party is the student council led by Nagisa, Seia, and Mika.",
        "messages": [HumanMessage(content="best girl in Tea Party faction Blue Archive")],
    }
    out = await node(state)
    blob = _all_content(llm.last_msgs)
    assert "Conversation context" in blob
    assert "tea_party_members" in blob
    assert "Tea Party is the student council" in blob
    # LLM may still emit legacy "task"; normalize_triage_mode maps it → classic.
    assert out["triage_mode"] == "classic"


@pytest.mark.asyncio
async def test_triage_does_not_set_response_directly_anymore():
    """Reply generation now belongs to chat_node; triage only stamps triage_mode.

    Also carries `token_usage`/`model_audit` (core_graph/model_roles/ladder.py) —
    the ladder's observability win closing triage's previously-unaccounted
    token usage — so this checks the field of interest, not exact dict shape.
    """
    llm = _StubLLM(json.dumps({"mode": "chat", "reply": "hi there"}))
    node = make_triage_node(_ctx(llm))
    out = await node({"user_query": "hi"})
    assert out["triage_mode"] == "chat"
    assert "response" not in out


@pytest.mark.asyncio
async def test_triage_defaults_to_classic_on_unparseable_response():
    llm = _StubLLM("not json")
    node = make_triage_node(_ctx(llm))
    out = await node({"user_query": "whatever"})
    assert out["triage_mode"] == "classic"


@pytest.mark.asyncio
async def test_triage_pins_catportfolio_ask_without_llm():
    """Visitor-ask wrapper is specialist — the LLM must not call classic search."""
    llm = _StubLLM(json.dumps({"mode": "classic", "reply": ""}))
    node = make_triage_node(_ctx(llm))
    wrapped = (
        "You are a helpful portfolio assistant.\n\n---\n\n"
        "Visitor Question: tell me about your game project\n\n"
        '[System: CatPortfolio ask turn (goal_class=scoped_ask). '
        'Page context: {"view":"tank","block_index":[],"tank_slugs":[]}]'
    )
    out = await node({"user_query": wrapped})
    assert out["triage_mode"] == "specialist"
    assert llm.calls == 0


# --------------------------------------------------------------------------- #
# chat_node: contextual replies
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_chat_node_always_calls_llm_with_context():
    llm = _StubLLM("Mika is the disciplinary committee chief and a Tea Party member.")
    node = make_chat_node(_ctx(llm))
    state = {
        "user_query": "mika?",
        "working_memory": {"tea_party_members": ["Nagisa", "Seia", "Mika"]},
        "last_summary": "Tea Party triumvirate: Nagisa, Seia, Mika.",
    }
    out = await node(state)
    blob = _all_content(llm.last_msgs)
    assert "Conversation context" in blob
    assert "tea_party_members" in blob
    assert out["response"]["status"] == "chat"
    assert "Mika" in out["response"]["message"]
    assert out["messages"][0].content == out["response"]["message"]


@pytest.mark.asyncio
async def test_chat_node_no_context_plain_greeting():
    llm = _StubLLM("Hello! How can I help you today?")
    node = make_chat_node(_ctx(llm))
    out = await node({"user_query": "hi"})
    assert llm.last_msgs[-1].content == "hi"
    assert out["response"]["message"] == "Hello! How can I help you today?"


@pytest.mark.asyncio
async def test_chat_node_flattens_list_content_blocks():
    llm = _StubLLM([{"type": "text", "text": "part one "}, {"type": "text", "text": "part two"}])
    node = make_chat_node(_ctx(llm))
    out = await node({"user_query": "hi"})
    assert out["response"]["message"] == "part one part two"


# --------------------------------------------------------------------------- #
# Optional harness/semantic memory enrichment (opt-in via config)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_harness_memory_enrichment_when_enabled(monkeypatch):
    import utils.server_config as sc
    monkeypatch.setattr(sc, "TRIAGE_CONFIG", {"harness_memory_enabled": True, "harness_memory_top_k": 2})

    async def _fake_search_memory(query, *, tenant_id, top_k):
        return {"status": "ok", "count": 1, "memories": [{"content": "Recalled: Mika likes tea."}]}

    import core.memory as core_memory
    monkeypatch.setattr(core_memory, "search_memory", _fake_search_memory)

    llm = _StubLLM(json.dumps({"mode": "chat", "reply": ""}))
    node = make_triage_node(_ctx(llm))
    await node({"user_query": "mika?"})
    blob = _all_content(llm.last_msgs)
    assert "Related memory" in blob
    assert "Recalled: Mika likes tea." in blob


@pytest.mark.asyncio
async def test_harness_memory_failure_is_swallowed(monkeypatch):
    import utils.server_config as sc
    monkeypatch.setattr(sc, "TRIAGE_CONFIG", {"harness_memory_enabled": True, "harness_memory_top_k": 2})

    async def _boom(query, *, tenant_id, top_k):
        raise RuntimeError("db down")

    import core.memory as core_memory
    monkeypatch.setattr(core_memory, "search_memory", _boom)

    llm = _StubLLM(json.dumps({"mode": "chat", "reply": "ok"}))
    node = make_triage_node(_ctx(llm))
    out = await node({"user_query": "mika?"})
    # Must not raise, must still classify.
    assert out["triage_mode"] == "chat"
