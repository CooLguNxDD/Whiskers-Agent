"""Unit tests for the GOAP goal node and its pure helpers (core_graph/node/goap_goal.py
and the new tracking helpers in core_graph/goap/goal_loop.py)."""

import pytest
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.goap_goal import make_goap_goal_node
from core_graph.goap.goal_loop import (
    evaluate_goal_facts,
    world_from_memory,
    build_goal_directive,
    accumulate_executed_ops,
    collect_fetched_ids,
)


_DASHED_IDS = [
    "3422783c-aabd-8138-ba5c-e7c13a7e7ea2",
    "3652783c-aabd-81a2-8a8a-f3d4671c9e9a",
    "3452783c-aabd-817d-8b49-fd783abb04b9",
    "3522783c-aabd-8113-b917-c0757788d371",
    "36f2783c-aabd-8150-8784-c2bc5d54b916",
]


def _fetch_envelope_for(dashed_id):
    """A notion-style fetch envelope that echoes the id only inside a dash-less URL."""
    dashless = dashed_id.replace("-", "")
    return {"url": f"https://app.notion.com/p/{dashless}?pvs=1", "text": "page content"}


def _fanout_result(dashed_ids):
    return {"status": "ok", "results": [_fetch_envelope_for(i) for i in dashed_ids]}


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #

class _FakeResp:
    def __init__(self, content):
        self.content = content


class _CountingLLM:
    """Records call count so tests can assert the deterministic path skips the LLM."""

    def __init__(self, content="{}"):
        self.calls = 0
        self._c = content
        self.last_human_content = None

    async def ainvoke(self, msgs):
        self.calls += 1
        if len(msgs) > 1:
            self.last_human_content = getattr(msgs[1], "content", None)
        return _FakeResp(self._c)


def _ctx(llm):
    return GraphRuntimeContext(llm=llm, context_params={}, api_url="", route_registry=None, checkpointer=None)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def test_evaluate_goal_facts_have_satisfaction():
    """have:X is achieved when X is present in the folded working memory world state."""
    world = world_from_memory({"record_id": 7})
    achieved, remaining = evaluate_goal_facts(["have:record_id", "have:message_id"], world, [])
    assert achieved == ["have:record_id"]
    assert remaining == ["have:message_id"]


def test_evaluate_goal_facts_did_satisfaction_and_proxy_strip():
    """did:OP is achieved when OP appears in executed ops, matched raw or proxy-prefix-stripped."""
    world = world_from_memory({})
    goal_facts = ["did:searchRecords", "did:notion-search", "did:sendMessage"]
    executed = ["searchRecords", "proxy_Notion-Dev__notion-search"]
    achieved, remaining = evaluate_goal_facts(goal_facts, world, executed)
    assert "did:searchRecords" in achieved
    assert "did:notion-search" in achieved          # matched via __ strip
    assert remaining == ["did:sendMessage"]


def test_evaluate_goal_facts_empty_goal():
    """No goal facts → both lists empty (drives the LLM-parity fallback in the node)."""
    achieved, remaining = evaluate_goal_facts([], world_from_memory({"x": 1}), ["op"])
    assert achieved == [] and remaining == []


def test_build_goal_directive_formats_remaining():
    directive = build_goal_directive(["did:sendMessage", "have:record_id"], "do the send")
    assert "sendMessage" in directive
    assert "record_id" in directive
    assert "do the send" in directive


def test_build_goal_directive_fallback_to_hint():
    assert build_goal_directive([], "just do x") == "just do x"
    assert build_goal_directive(None, "") == ""


def test_accumulate_executed_ops_dedup_and_order():
    out = accumulate_executed_ops(
        ["a"],
        [{"operation_id": "b"}, {"operation_id": "a"}],     # 'a' dedup
        [{"operation_id": "c"}, {"no_op": True}],
    )
    assert out == ["a", "b", "c"]


def test_accumulate_skips_failed_step():
    """A step whose positionally-aligned result failed must not have its
    operation_id counted as executed — otherwise a 404'd op would falsely
    satisfy its own did:<op> goal fact."""
    out = accumulate_executed_ops(
        None,
        [{"operation_id": "a"}, {"operation_id": "b"}],
        [{"operation_id": "a", "status": "ok"}, {"operation_id": "b", "status": "error"}],
    )
    assert out == ["a"]


def test_accumulate_skips_unexecuted_steps():
    """Plan steps with no corresponding step_results entry (never reached
    this iteration) must not be counted as executed."""
    out = accumulate_executed_ops(
        None,
        [{"operation_id": "a"}, {"operation_id": "b"}, {"operation_id": "c"}],
        [{"operation_id": "a", "status": "ok"}],
    )
    assert out == ["a"]


# --------------------------------------------------------------------------- #
# Node behavior
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_node_transactional_satisfied_llm_confirms_done():
    """All goal facts satisfied → the verifier is still consulted (no more deterministic
    short-circuit), but a plain transactional goal gets a prompt "done": true and
    terminates in a single verifier call — no spin."""
    llm = _CountingLLM('{"done": true, "reason": "action succeeded", "next_hint": ""}')
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "search and message",
        "goal_facts": ["have:record_id", "did:searchRecords"],
        "working_memory": {"record_id": 7},
        "executed_op_ids": ["searchRecords"],
        "iterations": 0,
        "max_iterations": 20,
        "step_results": [],
        "summary": "Found record 7",
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "done"
    assert res["response"]["status"] == "ok"
    assert res["remaining_goal_facts"] == []
    assert set(res["achieved_facts"]) == {"have:record_id", "did:searchRecords"}
    assert llm.calls == 1          # verifier consulted even though facts were satisfied


@pytest.mark.asyncio
async def test_node_research_goal_satisfied_facts_but_verifier_wants_more():
    """Shallow goal_facts are satisfied (single search), but this is an evaluative/
    superlative question — the verifier (mocked here as if it applied the RESEARCH
    COMPLETENESS rule) says not-done and the loop continues to gather more candidates,
    instead of stopping after the first shallow fact like the old deterministic branch."""
    llm = _CountingLLM(
        '{"done": false, "reason": "only one candidate looked up so far", '
        '"next_hint": "search for the other named members and compare"}'
    )
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "who is the best girl in the tea party",
        "goal_facts": ["did:web_search"],           # shallow — satisfied after one search
        "working_memory": {"query": "tea party"},
        "executed_op_ids": ["web_search"],
        "iterations": 1,
        "max_iterations": 20,
        "step_results": [{"operation_id": "web_search", "status": "ok"}],
        "summary": "found one member",
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "continue"
    assert llm.calls == 1
    assert res["remaining_goal_facts"] == []        # facts satisfied; verifier drove the gap
    assert "compare" in res["user_query"]            # next_hint became the directive


@pytest.mark.asyncio
async def test_node_verifier_includes_research_notes_in_prompt():
    """Verifier HumanMessage must include formatted prior-round findings when present."""
    llm = _CountingLLM('{"done": true, "reason": "enough evidence", "next_hint": ""}')
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "who is the best girl in the tea party",
        "goal_facts": ["did:web_search", "have:answer"],
        "working_memory": {"query": "tea party"},
        "executed_op_ids": ["web_search", "fetch_url"],
        "iterations": 2,
        "max_iterations": 20,
        "step_results": [{"operation_id": "fetch_url", "status": "ok"}],
        "summary": "gathered members",
        "research_notes": [
            {
                "iteration": 1,
                "directive": "search tea party",
                "ops": ["web_search"],
                "findings": '[{"text": "community ranks Mika highly"}]',
            },
        ],
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "done"
    assert llm.calls == 1
    assert "Prior round findings:" in (llm.last_human_content or "")
    assert "community ranks Mika highly" in (llm.last_human_content or "")


@pytest.mark.asyncio
async def test_node_remaining_llm_done():
    """Sub-goals remain but the LLM verifier confirms done → terminate."""
    llm = _CountingLLM('{"done": true, "reason": "answered", "next_hint": ""}')
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "search and message",
        "goal_facts": ["have:record_id", "did:sendMessage"],
        "working_memory": {"record_id": 7},
        "executed_op_ids": ["searchRecords"],
        "iterations": 0,
        "max_iterations": 20,
        "step_results": [],
        "summary": "done",
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "done"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_node_remaining_llm_continue_drives_gap():
    """Sub-goals remain and the LLM says not-done → continue toward the gap."""
    llm = _CountingLLM('{"done": false, "reason": "still need to send", "next_hint": "send the message"}')
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "search and message",
        "goal_facts": ["have:record_id", "did:sendMessage"],
        "working_memory": {"record_id": 7},
        "executed_op_ids": ["searchRecords"],
        "iterations": 0,
        "max_iterations": 20,
        "step_results": [],
        "summary": "found record",
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "continue"
    assert res["response"] is None                      # INVARIANT: cleared before re-plan
    assert res["remaining_goal_facts"] == ["did:sendMessage"]
    assert "sendMessage" in res["user_query"]           # focused directive
    assert res["plan"] == []


@pytest.mark.asyncio
async def test_node_budget_guard_done_skips_llm():
    """No structured goal facts + iteration budget exhausted → done without an LLM call."""
    llm = _CountingLLM()
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "g",
        "goal_facts": [],
        "iterations": 99,
        "max_iterations": 5,
        "response": None,
        "step_results": [],
        "summary": None,
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "done"
    assert res["response"]["status"] == "incomplete"
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_node_no_goal_facts_llm_parity_continue():
    """No goal facts + under budget → LLM-only check, parity with the old goal_check node."""
    llm = _CountingLLM('{"done": false, "reason": "more to do", "next_hint": "do x"}')
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "g",
        "goal_facts": [],
        "iterations": 0,
        "max_iterations": 20,
        "response": None,
        "step_results": [],
        "working_memory": {},
        "summary": None,
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "continue"
    assert res["user_query"] == "do x"                  # directive falls back to next_hint
    assert res["remaining_goal_facts"] == []
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_node_no_progress_guard_stops_stalled_verifier_spin():
    """Now that Branch C no longer short-circuits, a verifier that keeps returning
    "continue" while nothing new is gained (no new working_memory keys, no new
    executed ops) must not spin forever — the no-progress guard forces "done" after
    2 stalled rounds, independent of MAX_ITERATIONS."""
    llm = _CountingLLM('{"done": false, "reason": "keep trying", "next_hint": "try again"}')
    node = make_goap_goal_node(_ctx(llm))
    base_state = {
        "goal": "search and message",
        "goal_facts": ["have:record_id", "did:sendMessage"],
        "working_memory": {"record_id": 7},
        "executed_op_ids": ["searchRecords"],
        "iterations": 0,
        "max_iterations": 20,
        "step_results": [],           # nothing new each round → gained_nothing stays True
        "summary": "stuck",
        # Simulate this being a mid-loop re-entry where the verifier already said
        # "continue" once before with identical memory/ops (a stalled replan).
        "goal_loop_decision": "continue",
        "no_progress_rounds": 1,
    }
    res = await node(base_state)
    assert res["goal_loop_decision"] == "done"          # guard tripped on round 2
    assert res["no_progress_rounds"] == 2
    assert llm.calls == 0                                # never reached the verifier this round


@pytest.mark.asyncio
async def test_node_no_progress_guard_resets_on_real_progress():
    """When new working_memory/executed ops appear each round, the no-progress guard
    must never trip — genuine research turns keep gathering evidence."""
    llm = _CountingLLM('{"done": false, "reason": "need more", "next_hint": "keep searching"}')
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "who is the best girl in the tea party",
        "goal_facts": ["did:web_search", "have:answer"],
        "working_memory": {"record_id": 1},
        "executed_op_ids": ["web_search"],
        "iterations": 1,
        "max_iterations": 20,
        # New step result folds a new memory key + a new executed op each round.
        "step_results": [{"operation_id": "fetch_member_2", "status": "ok", "member_id": "abc"}],
        "summary": "gathering more candidates",
        "goal_loop_decision": "continue",
        "no_progress_rounds": 0,
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "continue"
    assert res["no_progress_rounds"] == 0               # progress made → guard stays reset
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_node_no_progress_uses_pre_round_memory_baseline():
    """round_summary folds step_results before goap_goal runs; without pre_round_memory
    the re-fold is idempotent so gained_nothing is always True even when values grew.

    Same op re-run does not grow executed_op_ids (dedup), so the ops side alone would
    look stalled — new memory values vs pre_round_memory must still count as progress.
    """
    llm = _CountingLLM('{"done": false, "reason": "need more", "next_hint": "keep searching"}')
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "who is the best girl in the tea party",
        "goal_facts": ["did:web_search", "have:answer"],
        # Post-round_summary memory (already folded member_id in).
        "working_memory": {"record_id": 1, "member_id": "abc"},
        # Pre-fold baseline stashed by round_summary.
        "pre_round_memory": {"record_id": 1},
        "executed_op_ids": ["web_search"],
        "plan": [{"operation_id": "web_search"}],
        "iterations": 1,
        "max_iterations": 20,
        # Re-folding the same results is idempotent vs working_memory.
        "step_results": [
            {"operation_id": "web_search", "status": "ok", "member_id": "abc"},
        ],
        "summary": "gathering more candidates",
        "goal_loop_decision": "continue",
        "no_progress_rounds": 1,  # one stalled round already
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "continue"
    assert res["no_progress_rounds"] == 0
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_node_no_progress_still_stops_when_pre_round_baseline_unchanged():
    """Pure no-op replan (identical memory + no new ops) still trips after 2 continues."""
    llm = _CountingLLM('{"done": false, "reason": "keep trying", "next_hint": "try again"}')
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "search and message",
        "goal_facts": ["have:record_id", "did:sendMessage"],
        "working_memory": {"record_id": 7},
        "pre_round_memory": {"record_id": 7},
        "executed_op_ids": ["searchRecords"],
        "iterations": 0,
        "max_iterations": 20,
        "step_results": [],
        "summary": "stuck",
        "goal_loop_decision": "continue",
        "no_progress_rounds": 1,
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "done"
    assert res["no_progress_rounds"] == 2
    assert llm.calls == 0


# --------------------------------------------------------------------------- #
# collect_fetched_ids (dash-insensitive substring match)
# --------------------------------------------------------------------------- #

def test_collect_fetched_ids_matches_dashless_url():
    """Dashed search ids are matched against the dash-less id embedded in fetched URLs."""
    step_results = [
        # search step: data.results candidate listing must NOT count as fetched
        {"status": "ok", "data": {"results": [{"id": _DASHED_IDS[4]}]}, "route": "CALL notion-search"},
        _fanout_result(_DASHED_IDS[:3]),
    ]
    found = collect_fetched_ids(step_results, _DASHED_IDS)
    assert set(found) == set(_DASHED_IDS[:3])
    assert _DASHED_IDS[4] not in found      # search listing excluded


def test_collect_fetched_ids_empty_without_candidates():
    assert collect_fetched_ids([_fanout_result(_DASHED_IDS)], []) == []


# --------------------------------------------------------------------------- #
# Count-aware multi-fetch gate
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_count_gate_replans_for_remaining():
    """Fewer than N fetched → continue with a pending_fetch_ids queue, no LLM call."""
    llm = _CountingLLM()
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "fetch 5 cat pages",
        "goal_facts": ["did:proxy_X__notion-search", "did:proxy_X__notion-fetch"],
        "executed_op_ids": ["proxy_X__notion-search", "proxy_X__notion-fetch"],
        "fanout_target": 5,
        "working_memory": {"page_ids": list(_DASHED_IDS)},
        "step_results": [_fanout_result(_DASHED_IDS[:2])],   # only 2 of 5 fetched
        "iterations": 0,
        "max_iterations": 20,
        "summary": "partial",
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "continue"
    assert res["response"] is None
    pending = res["working_memory"]["pending_fetch_ids"]
    assert set(pending) == set(_DASHED_IDS[2:])           # the 3 not yet fetched
    assert llm.calls == 0                                  # deterministic


@pytest.mark.asyncio
async def test_count_gate_done_when_target_met():
    """All N fetched → count-aware gate falls through with no LLM call of its own, but
    since Branch C (deterministic-done short-circuit) is gone, the shared verifier is
    still consulted before the loop actually ends."""
    llm = _CountingLLM('{"done": true, "reason": "all 5 fetched", "next_hint": ""}')
    node = make_goap_goal_node(_ctx(llm))
    state = {
        "goal": "fetch 5 cat pages",
        "goal_facts": ["did:proxy_X__notion-search", "did:proxy_X__notion-fetch"],
        "executed_op_ids": ["proxy_X__notion-search", "proxy_X__notion-fetch"],
        "fanout_target": 5,
        "working_memory": {"page_ids": list(_DASHED_IDS)},
        "step_results": [_fanout_result(_DASHED_IDS)],       # all 5 fetched
        "response": {"status": "ok", "summary": "done"},
        "iterations": 0,
        "max_iterations": 20,
        "summary": "done",
    }
    res = await node(state)
    assert res["goal_loop_decision"] == "done"
    assert res["response"]["status"] == "ok"
    assert "pending_fetch_ids" not in res["working_memory"]
    assert llm.calls == 1
