"""Route search integration tests — hybrid_search_routes() as a thin adapter
over the shared search engine (db_layer/embeddings/search_engine.py).

Generic RRF fusion / normalization / dense-vs-hybrid routing behavior is
covered once, generically, in test_search_engine.py — these tests only check
that the route adapter wires its SearchSpec (GOAP_CANDIDATE_DENYLIST row
filter, is_enabled extra filter, route embedding resolution) correctly.
"""
import pytest
from unittest.mock import AsyncMock, patch

from db_layer.embeddings.embeddings_routes import hybrid_search_routes


@pytest.mark.asyncio
async def test_hybrid_search_routes_disabled():
    with patch("core.llm_config_service.resolve_route_embedding", new_callable=AsyncMock) as mock_resolve, \
         patch("db_layer.embeddings.search_engine.embed_query_with", new_callable=AsyncMock) as mock_embed, \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", False), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}), \
         patch("db_layer.embeddings.search_engine._dense", new_callable=AsyncMock) as mock_dense, \
         patch("db_layer.embeddings.search_engine._sparse", new_callable=AsyncMock) as mock_sparse:

        mock_resolve.return_value = {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536}
        mock_embed.return_value = [0.1, 0.2]
        mock_dense.return_value = [{"plugin_id": "p1", "operation_id": "op1", "score": 0.9}]

        res = await hybrid_search_routes("test query", top_k=1)
        assert len(res) == 1
        assert res[0]["operation_id"] == "op1"
        mock_dense.assert_called_once()
        mock_sparse.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_search_routes_normalizes_scores():
    """Enabled hybrid path returns scores in (0.5, 1.0], not raw RRF ~0.03."""
    dense = [
        {"plugin_id": "p1", "operation_id": "op1", "score": 0.9},
        {"plugin_id": "p1", "operation_id": "op2", "score": 0.8},
    ]
    sparse = [
        {"plugin_id": "p1", "operation_id": "op1", "score": 2.0},
        {"plugin_id": "p1", "operation_id": "op3", "score": 1.0},
    ]
    with patch("core.llm_config_service.resolve_route_embedding", new_callable=AsyncMock) as mock_resolve, \
         patch("db_layer.embeddings.search_engine.embed_query_with", new_callable=AsyncMock) as mock_embed, \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", True), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}), \
         patch("db_layer.embeddings.search_engine.HYBRID_DENSE_TOP_N", 10), \
         patch("db_layer.embeddings.search_engine.HYBRID_SPARSE_TOP_N", 10), \
         patch("db_layer.embeddings.search_engine.HYBRID_RRF_K", 60), \
         patch("db_layer.embeddings.search_engine.HYBRID_FUSED_TOP_N", 25), \
         patch("db_layer.embeddings.search_engine._dense", new_callable=AsyncMock) as mock_dense, \
         patch("db_layer.embeddings.search_engine._sparse", new_callable=AsyncMock) as mock_sparse:

        mock_resolve.return_value = {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536}
        mock_embed.return_value = [0.1, 0.2]
        mock_dense.return_value = dense
        mock_sparse.return_value = sparse

        res = await hybrid_search_routes("test query", top_k=5)
        assert len(res) >= 1
        top = res[0]
        assert top["operation_id"] == "op1"
        assert 0.5 < top["score"] <= 1.0 + 1e-9
        assert top["score"] > 0.1


def test_route_row_filter_rejects_goap_denylist():
    """The row_filter hybrid_search_routes wires into the shared engine must
    reject GOAP_CANDIDATE_DENYLIST ops and accept everything else — this is
    what used to be an inline ``if (...) in DENYLIST: continue`` per query."""
    from db_layer.embeddings.embeddings_routes import GOAP_CANDIDATE_DENYLIST, _route_row_filter

    denylisted_plugin, denylisted_op = next(iter(GOAP_CANDIDATE_DENYLIST))
    assert _route_row_filter({"plugin_id": denylisted_plugin, "operation_id": denylisted_op}) is False
    assert _route_row_filter({"plugin_id": "p1", "operation_id": "allowed_op"}) is True


@pytest.mark.asyncio
async def test_hybrid_search_routes_filters_goap_denylist_end_to_end():
    """A denylisted op returned by dense search must never surface as a candidate."""
    from db_layer.embeddings.embeddings_routes import GOAP_CANDIDATE_DENYLIST

    denylisted_plugin, denylisted_op = next(iter(GOAP_CANDIDATE_DENYLIST))
    with patch("core.llm_config_service.resolve_route_embedding", new_callable=AsyncMock) as mock_resolve, \
         patch("db_layer.embeddings.search_engine.embed_query_with", new_callable=AsyncMock) as mock_embed, \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", False), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}):
        mock_resolve.return_value = {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536}
        mock_embed.return_value = [0.1, 0.2]

        # DB-hitting SQL is out of scope for a unit test; confirm the adapter
        # wires the real row_filter (asserted directly above) into the engine.
        with patch("db_layer.embeddings.search_engine._dense", new_callable=AsyncMock) as mock_dense:
            mock_dense.return_value = []
            await hybrid_search_routes("test query", top_k=5)
            row_filter = mock_dense.call_args.args[5]
            assert row_filter({"plugin_id": denylisted_plugin, "operation_id": denylisted_op}) is False
            assert row_filter({"plugin_id": "p1", "operation_id": "allowed_op"}) is True
