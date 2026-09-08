import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core_graph.goap.reranker import llm_rerank


@pytest.mark.asyncio
async def test_llm_rerank_disabled():
    candidates = [
        {"operation_id": f"op_{i}", "description": f"desc {i}"}
        for i in range(10)
    ]
    mock_get_llm = AsyncMock()
    with patch("core_graph.goap.reranker.RERANK_ENABLED", False), \
         patch("core_graph.goap.reranker.get_graph_core_llm", mock_get_llm):

        result, usage = await llm_rerank("query", candidates, top_k=3)
        assert len(result) == 3
        assert [c["operation_id"] for c in result] == ["op_0", "op_1", "op_2"]
        assert usage is None
        mock_get_llm.assert_not_called()


@pytest.mark.asyncio
async def test_llm_rerank_llm_exception_fallback():
    candidates = [
        {"operation_id": f"op_{i}", "description": f"desc {i}"}
        for i in range(10)
    ]
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM Error"))

    with patch("core_graph.goap.reranker.RERANK_ENABLED", True), \
         patch("core_graph.goap.reranker.get_graph_core_llm", AsyncMock(return_value=mock_llm)):

        result, _usage = await llm_rerank("query", candidates, top_k=3)
        assert len(result) == 3
        assert [c["operation_id"] for c in result] == ["op_0", "op_1", "op_2"]


@pytest.mark.asyncio
async def test_llm_rerank_timeout_fallback():
    candidates = [
        {"operation_id": f"op_{i}", "description": f"desc {i}"}
        for i in range(10)
    ]
    async def slow_llm(*args, **kwargs):
        import asyncio
        await asyncio.sleep(1.0)
        return "slow"

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=slow_llm)

    with patch("core_graph.goap.reranker.RERANK_ENABLED", True), \
         patch("core_graph.goap.reranker.RERANK_TIMEOUT_S", 0.05), \
         patch("core_graph.goap.reranker.get_graph_core_llm", AsyncMock(return_value=mock_llm)):

        result, _usage = await llm_rerank("query", candidates, top_k=3)
        assert len(result) == 3
        assert [c["operation_id"] for c in result] == ["op_0", "op_1", "op_2"]


@pytest.mark.asyncio
async def test_llm_rerank_valid_reorder_and_backfill():
    candidates = [
        {"operation_id": f"op_{i}", "description": f"desc {i}"}
        for i in range(10)
    ]
    mock_response = MagicMock()
    mock_response.content = '{"ranked_indices": [3, 1]}'

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    with patch("core_graph.goap.reranker.RERANK_ENABLED", True), \
         patch("core_graph.goap.reranker.get_graph_core_llm", AsyncMock(return_value=mock_llm)):

        # top_k=4, LLM only gives indices 3 and 1 (op_2 and op_0). Backfill should add op_1, op_3...
        result, _usage = await llm_rerank("query", candidates, top_k=4)
        assert len(result) == 4
        assert [c["operation_id"] for c in result] == ["op_2", "op_0", "op_1", "op_3"]


@pytest.mark.asyncio
async def test_llm_rerank_garbage_response_fallback():
    candidates = [
        {"operation_id": f"op_{i}", "description": f"desc {i}"}
        for i in range(5)
    ]
    mock_response = MagicMock()
    mock_response.content = "Invalid non-json output"

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    with patch("core_graph.goap.reranker.RERANK_ENABLED", True), \
         patch("core_graph.goap.reranker.get_graph_core_llm", AsyncMock(return_value=mock_llm)):

        result, _usage = await llm_rerank("query", candidates, top_k=3)
        assert len(result) == 3
        assert [c["operation_id"] for c in result] == ["op_0", "op_1", "op_2"]
