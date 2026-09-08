"""Unit tests for the planner's decompose-first hints: sub-task block injection into
the GOAP and linear prompts, and decompose_seed_values merge precedence
(core_graph/node/planner.py)."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core_graph.node.planner import make_planner_node
from core_graph.node.context import GraphRuntimeContext


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _SeqLLM:
    """Returns queued responses (or raises queued exceptions); records all invocations."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.invocations = []

    async def ainvoke(self, msgs):
        self.invocations.append(msgs)
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return _FakeResp(out)


def _ctx(llm):
    return GraphRuntimeContext(llm=llm, context_params={}, api_url="", route_registry=None, checkpointer=None)


def _cand(op_id, score=0.9):
    return {
        "plugin_id": "p", "operation_id": op_id, "path": f"/{op_id}", "path_template": f"/{op_id}",
        "method": "GET", "description": f"{op_id} desc", "score": score,
        "parameters": {"properties": {}, "required": []}, "is_fast_path": False, "metadata": {},
    }


@pytest.fixture(autouse=True)
def _stub_pool_and_policy(monkeypatch):
    """Silence the chat-pool and step-model-policy DB lookups."""
    async def fake_list_pool(kind="chat"):
        return []
    import core.llm_config_service as svc
    monkeypatch.setattr(svc, "list_pool", fake_list_pool)

    async def fake_policy():
        return {"strategy": "off", "parallel_enabled": False}
    import db_layer.step_model_settings_store as store
    monkeypatch.setattr(store, "get_step_model_policy", fake_policy)


def _goap_payload(op_id, seeds):
    return json.dumps({
        "intent": "do the thing",
        "goal": [f"did:{op_id}"],
        "seed_facts": [],
        "seed_values": seeds,
        "confidence": 1.0,
        "clarifying_questions": [],
    })


@pytest.mark.asyncio
async def test_goap_prompt_contains_subtask_block_and_seed_merge():
    llm = _SeqLLM([_goap_payload("op_a", {"query": "llm-win"})])
    node = make_planner_node(_ctx(llm))
    out = await node({
        "user_query": "do the thing",
        "candidates": [_cand("op_a")],
        "sub_tasks": ["find the thing", "act on the thing"],
        "decompose_seed_values": {"query": "decomp-loses", "extra": "kept"},
        "messages": [],
    })

    human = llm.invocations[0][-1].content
    assert "Decomposed sub-tasks (retrieval was run per sub-task):" in human
    assert "- find the thing" in human
    assert "Preliminary seed values:" in human

    # planner's candidate-aware extraction wins on conflict; decompose-only keys kept
    assert out["seed_values"] == {"query": "llm-win", "extra": "kept"}
    assert out["plan"], "GOAP should have produced a plan for the single did: goal"


@pytest.mark.asyncio
async def test_goap_prompt_no_subtask_block_when_absent():
    llm = _SeqLLM([_goap_payload("op_a", {})])
    node = make_planner_node(_ctx(llm))
    await node({
        "user_query": "do the thing",
        "candidates": [_cand("op_a")],
        "messages": [],
    })
    human = llm.invocations[0][-1].content
    assert "Decomposed sub-tasks" not in human


@pytest.mark.asyncio
async def test_linear_fallback_prompt_contains_subtask_block():
    # First (GOAP) LLM call raises → planner falls back to the linear prompt.
    linear = json.dumps({
        "name": "wf", "reasoning": "r", "confidence": 0.9, "outputs": {},
        "instructions": [{"intent": "x", "operation_id": "op_a", "plugin_id": "p", "notes": ""}],
    })
    llm = _SeqLLM([RuntimeError("goap llm down"), linear])
    node = make_planner_node(_ctx(llm))
    out = await node({
        "user_query": "do the thing",
        "candidates": [_cand("op_a")],
        "sub_tasks": ["find the thing"],
        "messages": [],
    })
    assert out["instruction_set"]
    linear_human = llm.invocations[1][-1].content
    assert "Decomposed sub-tasks (retrieval was run per sub-task):" in linear_human
    assert "- find the thing" in linear_human


@pytest.mark.asyncio
async def test_linear_fallback_prompt_includes_conversation_context():
    """GOAP miss still sees the two-sided transcript, not only 'User wants: search up!'."""
    linear = json.dumps({
        "name": "wf", "reasoning": "r", "confidence": 0.9, "outputs": {},
        "instructions": [{"intent": "x", "operation_id": "op_a", "plugin_id": "p", "notes": ""}],
    })
    llm = _SeqLLM([RuntimeError("goap llm down"), linear])
    node = make_planner_node(_ctx(llm))
    await node({
        "user_query": "search up!",
        "candidates": [_cand("op_a")],
        "messages": [
            HumanMessage(content="who is mika misono?"),
            AIMessage(content="Mika Misono is a Blue Archive character."),
        ],
        "last_summary": None,
        "working_memory": {},
    })
    linear_human = llm.invocations[1][-1].content
    assert "Conversation so far:" in linear_human
    assert "who is mika misono?" in linear_human
    assert "Mika Misono is a Blue Archive character." in linear_human
    assert "User wants: search up!" in linear_human


@pytest.mark.asyncio
async def test_linear_fallback_with_instructions_sets_goal():
    """A turn that falls back to the linear planner (GOAP raised/returned no
    steps) must still populate state["goal"], otherwise retry_router can
    never route a terminal downstream error to goap_goal recovery and the
    whole run hard-halts on the first non-whitelisted failure."""
    linear = json.dumps({
        "name": "wf", "reasoning": "r", "confidence": 0.9, "outputs": {},
        "instructions": [{"intent": "x", "operation_id": "op_a", "plugin_id": "p", "notes": ""}],
    })
    llm = _SeqLLM([RuntimeError("goap llm down"), linear])
    node = make_planner_node(_ctx(llm))
    out = await node({
        "user_query": "do the thing",
        "candidates": [_cand("op_a")],
        "messages": [],
    })
    assert out["goal"] == "do the thing"


@pytest.mark.asyncio
async def test_linear_fallback_no_instructions_sets_goal():
    """Same requirement for the deeper fallback: GOAP raises AND the linear
    LLM returns no usable instructions, so the best-candidate-match plan is
    used — it must also carry state["goal"]."""
    linear_no_instructions = json.dumps({
        "name": "wf", "reasoning": "r", "confidence": 0.9, "outputs": {},
    })
    llm = _SeqLLM([RuntimeError("goap llm down"), linear_no_instructions])
    node = make_planner_node(_ctx(llm))
    out = await node({
        "user_query": "do the thing",
        "candidates": [_cand("op_a")],
        "messages": [],
    })
    assert out["goal"] == "do the thing"
