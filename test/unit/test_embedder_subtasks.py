"""Unit tests for the embedder node's decompose-first behavior: per-sub-task
retrieval fan-out and the merged candidate-pool cap (core_graph/node/embedder.py)."""

import pytest

from core_graph.node.embedder import make_embedder_node
from core_graph.node.context import GraphRuntimeContext
from utils.server_config import CANDIDATE_POOL_MAX


def _ctx():
    return GraphRuntimeContext(llm=None, context_params={}, api_url="", route_registry=None, checkpointer=None)


def _cand(op_id, score):
    return {
        "plugin_id": "p", "operation_id": op_id, "path": f"/{op_id}", "method": "GET",
        "description": op_id, "score": score, "parameters": {"properties": {}},
    }


@pytest.fixture
def search_spy(monkeypatch):
    """Patch db_layer search_routes; records queries, returns canned per-query results."""
    calls = []
    results_by_query = {}

    async def fake_search_routes(query, top_k=3):
        calls.append((query, top_k))
        return list(results_by_query.get(query, []))

    import db_layer.embeddings.embeddings_routes as er
    monkeypatch.setattr(er, "search_routes", fake_search_routes)
    return calls, results_by_query


@pytest.mark.asyncio
async def test_sub_tasks_drive_one_search_per_intent(search_spy):
    calls, results = search_spy
    results["search notion pages"] = [_cand("notion_search", 0.9)]
    results["send a message"] = [_cand("send_message", 0.8)]

    node = make_embedder_node(_ctx())
    out = await node({
        "user_query": "search notion and message the contact",
        "sub_tasks": ["search notion pages", "send a message"],
    })

    assert [q for q, _ in calls] == ["search notion pages", "send a message"]
    ops = [c["operation_id"] for c in out["candidates"]]
    assert ops == ["notion_search", "send_message"]  # deduped, score-desc
    assert out["confidence"] == 0.9


@pytest.mark.asyncio
async def test_sub_tasks_take_precedence_over_goal_and_user_query(search_spy):
    calls, results = search_spy
    results["sub task a"] = [_cand("op_a", 0.5)]

    node = make_embedder_node(_ctx())
    await node({
        "user_query": "raw query",
        "goal": ["goal intent"],
        "sub_tasks": ["sub task a"],
    })
    assert [q for q, _ in calls] == ["sub task a"]


@pytest.mark.asyncio
async def test_empty_sub_tasks_falls_back_to_goal_then_user_query(search_spy):
    calls, _ = search_spy
    node = make_embedder_node(_ctx())

    await node({"user_query": "raw query", "sub_tasks": [], "goal": ["goal intent"]})
    assert [q for q, _ in calls] == ["goal intent"]

    calls.clear()
    await node({"user_query": "raw query", "sub_tasks": None, "goal": None})
    assert [q for q, _ in calls] == ["raw query"]


@pytest.mark.asyncio
async def test_pool_cap_admits_highest_score_new_only(search_spy):
    """New candidates beyond CANDIDATE_POOL_MAX are trimmed lowest-score-first."""
    calls, results = search_spy
    overflow = 5
    results["big"] = [_cand(f"op_{i}", 1.0 - i * 0.01) for i in range(CANDIDATE_POOL_MAX + overflow)]

    node = make_embedder_node(_ctx())
    out = await node({"user_query": "big", "sub_tasks": ["big"]})

    assert len(out["candidates"]) == CANDIDATE_POOL_MAX
    ops = {c["operation_id"] for c in out["candidates"]}
    # highest-score entries kept, lowest trimmed
    assert "op_0" in ops
    assert f"op_{CANDIDATE_POOL_MAX + overflow - 1}" not in ops


@pytest.mark.asyncio
async def test_pool_cap_never_evicts_existing_pool_entries(search_spy):
    """Pre-existing candidate_pool entries survive even when the pool is already at cap."""
    calls, results = search_spy
    existing = [_cand(f"pool_{i}", 0.01) for i in range(CANDIDATE_POOL_MAX)]
    results["q"] = [_cand("shiny_new", 0.99)]

    node = make_embedder_node(_ctx())
    out = await node({"user_query": "q", "sub_tasks": ["q"], "candidate_pool": existing})

    ops = {c["operation_id"] for c in out["candidates"]}
    assert all(f"pool_{i}" in ops for i in range(CANDIDATE_POOL_MAX))
    # pool already at cap → new entry not admitted, existing never evicted
    assert "shiny_new" not in ops
    assert len(out["candidates"]) == CANDIDATE_POOL_MAX
