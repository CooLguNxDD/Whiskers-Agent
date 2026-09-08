import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core_graph.node.embedder import make_embedder_node


@pytest.mark.asyncio
async def test_embedder_node_calls_rerank_and_preserves_candidate_pool():
    ctx = MagicMock()
    ctx.route_registry = None
    node = make_embedder_node(ctx)

    state = {
        "user_query": "find contact info",
        "sub_tasks": ["search contact"],
        "candidate_pool": [],
    }

    mock_search_results = [
        {"plugin_id": "p1", "operation_id": f"op_{i}", "score": 1.0 - (i * 0.1)}
        for i in range(10)
    ]

    mock_reranked = [
        {"plugin_id": "p1", "operation_id": "op_5", "score": 0.5},
        {"plugin_id": "p1", "operation_id": "op_0", "score": 1.0},
    ]

    mock_search = AsyncMock(return_value=mock_search_results)
    mock_rerank = AsyncMock(return_value=(mock_reranked, None))

    with patch("db_layer.embeddings.embeddings_routes.search_routes", mock_search), \
         patch("core_graph.goap.reranker.llm_rerank", mock_rerank):

        out = await node(state)

        # Check llm_rerank call
        mock_rerank.assert_called_once()
        args, kwargs = mock_rerank.call_args
        assert args[0] == "find contact info"  # primary query from goal / user_query
        assert len(args[1]) == 10  # fused candidates before rerank

        # Verify output dict: candidates keeps the FULL pool (GOAP search/validation
        # downstream needs every candidate available — see test_embedder_subtasks.py
        # pool-cap contract), reordered with the reranked top-K folded to the front;
        # candidate_pool keeps the full pre-rerank pool too.
        assert len(out["candidates"]) == 10
        assert [c["operation_id"] for c in out["candidates"][:2]] == ["op_5", "op_0"]
        assert {c["operation_id"] for c in out["candidates"]} == {f"op_{i}" for i in range(10)}
        assert len(out["candidate_pool"]) == 10
        assert out["confidence"] == 1.0
