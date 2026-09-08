"""Unit tests for summary_node (core_graph/node/summary.py).

Covers the goal-aware question fix: summary_node must synthesize an answer to the
stable `original_query` (falling back to `goal`, then the possibly-rewritten
`user_query`) rather than the goal loop's final, focused sub-directive — and must
fold `working_memory` into the prompt alongside `step_results`.
"""

import json
import pytest
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.summary import make_summary_node


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _CapturingLLM:
    """Records the HumanMessage content passed to ainvoke() for assertions."""

    def __init__(self, content):
        self.calls = 0
        self.last_human_content = None
        self._c = content

    async def ainvoke(self, msgs):
        self.calls += 1
        # msgs = [SystemMessage(SUMMARY_PROMPT), HumanMessage(...)]
        self.last_human_content = msgs[1].content
        return _FakeResp(self._c)


def _ctx(llm):
    return GraphRuntimeContext(llm=llm, context_params={}, api_url="", route_registry=None, checkpointer=None)


_SUMMARY_JSON = json.dumps(
    {
        "summary": "## Overview\nNo single objective best; evidence leans toward Nagisa.\n\n## Comparison\n...",
        "content": {"mika": {}, "nagisa": {}, "seia": {}},
        "carry": {"mika_name": "Misono Mika"},
    }
)


@pytest.mark.asyncio
async def test_summary_folds_artifacts_into_response_and_prompt():
    """Offloaded artifact refs must appear in new_response and the LLM prompt."""
    llm = _CapturingLLM(_SUMMARY_JSON)
    node = make_summary_node(_ctx(llm))
    state = {
        "user_query": "show the diff",
        "original_query": "show the diff",
        "working_memory": {},
        "step_results": [{"operation_id": "get_activity", "status": "ok"}],
        "plan": [],
        "artifacts": [
            {
                "short_id": "art_diff_001",
                "kind": "diff",
                "bytes": 12345,
                "path": "step_results[0].data.unidiffPatch",
                "object_key": "secret/key",
            }
        ],
    }
    res = await node(state)
    assert "art_diff_001" in llm.last_human_content
    assert "fetch_artifact" in llm.last_human_content
    arts = res["response"]["artifacts"]
    assert len(arts) == 1
    assert arts[0]["short_id"] == "art_diff_001"
    assert "object_key" not in arts[0]
    assert arts[0]["console_path"].endswith("art_diff_001")


@pytest.mark.asyncio
async def test_summary_uses_original_query_not_mutated_user_query():
    """The goal loop rewrites user_query to a focused sub-directive on every replan;
    summary_node must synthesize against the stable original_query instead."""
    llm = _CapturingLLM(_SUMMARY_JSON)
    node = make_summary_node(_ctx(llm))
    state = {
        "user_query": "Search for community discussions or character profiles regarding Mika, Nagisa, and Seia individually",
        "original_query": "who is the best girl in the tea party in blue archive",
        "goal": ["did:web_search", "have:answer"],
        "working_memory": {"query": "tea party", "mika_name": "Misono Mika"},
        "step_results": [{"operation_id": "web_search", "status": "ok"}],
        "plan": [],
    }
    res = await node(state)
    assert llm.calls == 1
    assert "who is the best girl in the tea party" in llm.last_human_content
    # The rewritten sub-directive must NOT be what's presented as "User request".
    assert "individually" not in llm.last_human_content.split("Known facts")[0]
    assert "mika_name" in llm.last_human_content          # working_memory folded in
    assert res["response"]["summary"].startswith("## Overview")
    assert res["response"]["carry"] == {"mika_name": "Misono Mika"}


@pytest.mark.asyncio
async def test_summary_falls_back_to_goal_when_no_original_query():
    """Turns that predate/bypass original_query fall back to `goal`, still preferring
    it over the (possibly rewritten) user_query."""
    llm = _CapturingLLM(_SUMMARY_JSON)
    node = make_summary_node(_ctx(llm))
    state = {
        "user_query": "focused sub-directive text",
        "goal": "who is the best girl in the tea party",
        "working_memory": {},
        "step_results": [],
        "plan": [],
    }
    await node(state)
    assert "who is the best girl" in llm.last_human_content


@pytest.mark.asyncio
async def test_summary_falls_back_to_user_query_when_nothing_else_set():
    """No original_query and no goal → last resort is the raw user_query (parity with
    pre-fix behavior for turns with neither field)."""
    llm = _CapturingLLM(_SUMMARY_JSON)
    node = make_summary_node(_ctx(llm))
    state = {
        "user_query": "create one record",
        "working_memory": {},
        "step_results": [{"operation_id": "createRecord", "status": "ok"}],
        "plan": [],
    }
    await node(state)
    assert "create one record" in llm.last_human_content


@pytest.mark.asyncio
async def test_summary_json_contract_still_parses():
    """The JSON contract (summary/content/carry) must still parse into the response dict."""
    llm = _CapturingLLM(_SUMMARY_JSON)
    node = make_summary_node(_ctx(llm))
    state = {
        "user_query": "q",
        "original_query": "q",
        "working_memory": {},
        "step_results": [],
        "plan": [],
    }
    res = await node(state)
    assert res["response"]["content"] == {"mika": {}, "nagisa": {}, "seia": {}}
    assert res["summary"] == res["response"]["summary"]


@pytest.mark.asyncio
async def test_summary_folds_prior_research_notes_into_prompt():
    """Final synthesis must include formatted prior-round findings from research_notes."""
    llm = _CapturingLLM(_SUMMARY_JSON)
    node = make_summary_node(_ctx(llm))
    state = {
        "user_query": "compare remaining members",
        "original_query": "who is the best girl in the tea party",
        "working_memory": {"query": "tea party"},
        "step_results": [{"operation_id": "web_search", "status": "ok"}],
        "plan": [],
        "research_notes": [
            {
                "iteration": 1,
                "directive": "search tea party",
                "ops": ["web_search"],
                "findings": '[{"hit": "Misono Mika is widely discussed"}]',
            },
            {
                "iteration": 2,
                "directive": "look up Nagisa",
                "ops": ["fetch_url"],
                "findings": '[{"hit": "Nagisa Kirifuji details"}]',
            },
        ],
    }
    await node(state)
    assert "Prior round notes:" in llm.last_human_content
    assert "Misono Mika is widely discussed" in llm.last_human_content
    assert "Nagisa Kirifuji details" in llm.last_human_content
    assert "who is the best girl in the tea party" in llm.last_human_content


@pytest.mark.asyncio
async def test_summary_preserves_specialist_bake_envelope():
    """Specialist bake response must not be rewritten by empty-step LLM summary."""
    llm = _CapturingLLM(
        json.dumps(
            {
                "summary": "No step results were provided for the portfolio agent execution.",
                "content": "",
                "carry": {},
            }
        )
    )
    node = make_summary_node(_ctx(llm))
    layout = {
        "version": 1,
        "meta": {"audience": "peer", "scopedProjectCount": 3},
        "blocks": [{"type": "hero"}, {"type": "projectGrid"}],
    }
    state = {
        "user_query": "bake portfolio for SpaceX",
        "original_query": "bake portfolio for SpaceX",
        "working_memory": {},
        "step_results": [],
        "plan": [],
        "response": {
            "status": "ok",
            "specialist": True,
            "goal_class": "bake_for_job",
            "short_id": "spacex_senior_full_102",
            "portfolio_job_id": "spacex_senior_full_102",
            "query_param": "j=spacex_senior_full_102",
            "summary": "Baked job portfolio short_id=spacex_senior_full_102. Open with ?j=spacex_senior_full_102.",
            "message": "Baked job portfolio short_id=spacex_senior_full_102. Open with ?j=spacex_senior_full_102.",
            "layout": layout,
            "carry": {"layout": layout},
        },
    }
    res = await node(state)
    assert llm.calls == 0  # must not invoke LLM
    assert "spacex_senior_full_102" in res["summary"]
    assert "No step results" not in res["summary"]
    assert res["response"]["short_id"] == "spacex_senior_full_102"
    assert res["response"]["layout"]["blocks"][0]["type"] == "hero"
    assert res["response"]["carry"]["layout"]["version"] == 1
