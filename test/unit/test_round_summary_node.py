"""Unit tests for the deterministic round_summary node (core_graph/node/round_summary.py).

No LLM — asserts research_notes append/accumulate, layout capture into working_memory,
and bounded caps via append_research_note. Also covers MinIO artifact offload wiring.
"""

from unittest.mock import AsyncMock, patch

import pytest
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.round_summary import make_round_summary_node
from core_graph.goap.goal_loop import MAX_RESEARCH_NOTES, RESEARCH_NOTE_CHARS


def _ctx():
    return GraphRuntimeContext(
        llm=None, context_params={}, api_url="", route_registry=None, checkpointer=None
    )


@pytest.fixture(autouse=True)
def _disable_artifact_offload(monkeypatch):
    """Keep legacy round_summary tests free of MinIO side effects."""
    monkeypatch.setattr(
        "utils.server_config.ARTIFACT_OFFLOAD_ENABLED", False, raising=False
    )


@pytest.mark.asyncio
async def test_round_summary_appends_note_with_directive_ops_findings():
    node = make_round_summary_node(_ctx())
    state = {
        "iterations": 0,
        "user_query": "search for tea party members",
        "plan": [
            {"operation_id": "web_search"},
            {"operation_id": "fetch_url"},
        ],
        "step_results": [
            {"operation_id": "web_search", "status": "ok", "data": {"hits": 3}},
            {"operation_id": "fetch_url", "status": "ok", "text": "Mika is popular"},
        ],
        "working_memory": {"seed": 1},
        "research_notes": [],
    }
    res = await node(state)
    notes = res["research_notes"]
    assert len(notes) == 1
    note = notes[0]
    assert note["iteration"] == 1
    assert note["directive"] == "search for tea party members"
    assert note["ops"] == ["web_search", "fetch_url"]
    assert "Mika is popular" in note["findings"]
    assert res["last_summary"]
    assert "web_search" in res["last_summary"]
    # Pre-fold baseline for goap_goal no-progress guard.
    assert res["pre_round_memory"] == {"seed": 1}
    assert res["working_memory"] != res["pre_round_memory"]


@pytest.mark.asyncio
async def test_round_summary_accumulates_across_rounds():
    node = make_round_summary_node(_ctx())
    state1 = {
        "iterations": 0,
        "user_query": "first directive",
        "plan": [{"operation_id": "web_search"}],
        "step_results": [{"operation_id": "web_search", "status": "ok", "q": "a"}],
        "working_memory": {},
        "research_notes": [],
    }
    res1 = await node(state1)
    state2 = {
        "iterations": 1,
        "user_query": "second directive",
        "plan": [{"operation_id": "fetch_url"}],
        "step_results": [{"operation_id": "fetch_url", "status": "ok", "q": "b"}],
        "working_memory": res1["working_memory"],
        "research_notes": res1["research_notes"],
    }
    res2 = await node(state2)
    notes = res2["research_notes"]
    assert len(notes) == 2
    assert notes[0]["directive"] == "first directive"
    assert notes[1]["directive"] == "second directive"
    assert notes[1]["iteration"] == 2


@pytest.mark.asyncio
async def test_round_summary_captures_layout_into_working_memory():
    node = make_round_summary_node(_ctx())
    layout = {
        "version": 1,
        "meta": {"title": "t"},
        "blocks": [{"type": "hero", "props": {}}],
    }
    state = {
        "iterations": 0,
        "user_query": "redesign portfolio",
        "plan": [{"operation_id": "design_layout"}],
        "step_results": [{"status": "ok", "layout": layout}],
        "working_memory": {},
        "research_notes": [],
    }
    res = await node(state)
    assert res["working_memory"].get("layout") == layout


@pytest.mark.asyncio
async def test_round_summary_no_llm_and_folds_ids():
    """Factory accepts None llm; node must not call any model."""
    node = make_round_summary_node(_ctx())
    state = {
        "iterations": 0,
        "user_query": "get record",
        "plan": [{"operation_id": "getRecord"}],
        "step_results": [{"status": "ok", "record_id": 42}],
        "working_memory": {},
        "research_notes": None,
    }
    res = await node(state)
    assert res["working_memory"].get("record_id") == 42
    assert len(res["research_notes"]) == 1


@pytest.mark.asyncio
async def test_round_summary_respects_max_notes_cap():
    node = make_round_summary_node(_ctx())
    notes = [
        {"iteration": i, "directive": f"d{i}", "ops": [], "findings": "[]"}
        for i in range(MAX_RESEARCH_NOTES)
    ]
    state = {
        "iterations": MAX_RESEARCH_NOTES,
        "user_query": "overflow",
        "plan": [{"operation_id": "op"}],
        "step_results": [{"x": 1}],
        "working_memory": {},
        "research_notes": notes,
    }
    res = await node(state)
    assert len(res["research_notes"]) == MAX_RESEARCH_NOTES
    # Oldest dropped; newest is the overflow directive.
    assert res["research_notes"][-1]["directive"] == "overflow"
    assert res["research_notes"][0]["iteration"] == 1  # old 0 dropped


@pytest.mark.asyncio
async def test_round_summary_findings_char_bounded():
    node = make_round_summary_node(_ctx())
    huge = "x" * (RESEARCH_NOTE_CHARS + 5000)
    state = {
        "iterations": 0,
        "user_query": "big",
        "plan": [{"operation_id": "web_search"}],
        "step_results": [{"blob": huge}],
        "working_memory": {},
        "research_notes": [],
    }
    res = await node(state)
    assert len(res["research_notes"][0]["findings"]) <= RESEARCH_NOTE_CHARS


@pytest.mark.asyncio
async def test_round_summary_offloads_large_fields(monkeypatch):
    """When offload is on, large strings become markers and artifacts accumulate."""
    monkeypatch.setattr("utils.server_config.ARTIFACT_OFFLOAD_ENABLED", True)
    monkeypatch.setattr("utils.server_config.ARTIFACT_OFFLOAD_MIN_FIELD_BYTES", 100)
    monkeypatch.setattr("utils.server_config.ARTIFACT_OFFLOAD_MAX_PER_ROUND", 8)

    big = "Q" * 500
    ref = {
        "kind": "log",
        "short_id": "art_log_001",
        "bytes": 500,
        "content_type": "text/markdown",
        "session_id": "sess",
        "path": "step_results[0].blob",
        "console_path": "/api/artifacts/session_gated/art_log_001",
    }

    async def _extract(step_results, **kwargs):
        slim = [{"status": "ok", "blob": f"[offloaded: short_id=art_log_001 ({len(big)} bytes)]"}]
        return slim, [ref]

    node = make_round_summary_node(_ctx())
    state = {
        "iterations": 0,
        "user_query": "get logs",
        "session_id": "sess",
        "plan": [{"operation_id": "get_activity"}],
        "step_results": [{"status": "ok", "blob": big}],
        "working_memory": {},
        "research_notes": [],
        "artifacts": [{"short_id": "art_prior_001", "kind": "diff", "bytes": 1}],
        "response": {
            "status": "ok",
            "steps_executed": 1,
            "data": {"status": "ok", "blob": big},
        },
    }
    with patch(
        "core.artifact_store.store.extract_large_artifacts",
        new_callable=AsyncMock,
        side_effect=_extract,
    ):
        res = await node(state)

    assert res["step_results"][0]["blob"].startswith("[offloaded:")
    # prior + at least one new ref (step_results walk; response may also offload)
    assert len(res["artifacts"]) >= 2
    assert any(a.get("short_id") == "art_log_001" for a in res["artifacts"])
    # response.data rebound to slim step_results so finalize stays small
    assert res["response"]["data"]["blob"].startswith("[offloaded:")


@pytest.mark.asyncio
async def test_round_summary_offload_disabled_is_noop(monkeypatch):
    monkeypatch.setattr("utils.server_config.ARTIFACT_OFFLOAD_ENABLED", False)
    node = make_round_summary_node(_ctx())
    big = "Z" * 9000
    state = {
        "iterations": 0,
        "user_query": "x",
        "plan": [{"operation_id": "op"}],
        "step_results": [{"blob": big}],
        "working_memory": {},
        "research_notes": [],
        "artifacts": [],
    }
    res = await node(state)
    assert res["step_results"][0]["blob"] == big
    assert res["artifacts"] == []
