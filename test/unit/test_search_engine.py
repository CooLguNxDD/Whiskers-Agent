"""Unit tests for the unified hybrid/dense embedding search engine
(db_layer/embeddings/search_engine.py) — the shared choke point every
embedding search (routes, memory, search index, messages, patients, unity)
now funnels through instead of hand-rolling dense-only queries."""

import pytest
from unittest.mock import AsyncMock, patch

from db_layer.embeddings.search_engine import (
    SearchSpec,
    normalize_rrf_scores,
    rrf_fuse,
    search,
)


# ---------------------------------------------------------------------------
# rrf_fuse / normalize_rrf_scores — generalized (caller-supplied key_fn)
# ---------------------------------------------------------------------------

_ROUTE_KEY = lambda c: (c["plugin_id"], c["operation_id"])  # noqa: E731
_ID_KEY = lambda c: c["id"]  # noqa: E731


def test_rrf_fuse_disjoint_tuple_key():
    dense = [
        {"plugin_id": "p1", "operation_id": "op1", "score": 0.9},
        {"plugin_id": "p1", "operation_id": "op2", "score": 0.8},
    ]
    sparse = [
        {"plugin_id": "p2", "operation_id": "op3", "score": 1.5},
        {"plugin_id": "p2", "operation_id": "op4", "score": 1.1},
    ]
    fused = rrf_fuse(dense, sparse, _ROUTE_KEY, k=60)
    assert len(fused) == 4
    keys = [_ROUTE_KEY(c) for c in fused]
    assert ("p1", "op1") in keys
    assert ("p2", "op3") in keys


def test_rrf_fuse_overlapping_tuple_key():
    dense = [
        {"plugin_id": "p1", "operation_id": "op1", "score": 0.9},  # rank 1 -> 1/61
        {"plugin_id": "p1", "operation_id": "op2", "score": 0.8},  # rank 2 -> 1/62
    ]
    sparse = [
        {"plugin_id": "p1", "operation_id": "op2", "score": 2.0},  # rank 1 -> 1/61
        {"plugin_id": "p1", "operation_id": "op3", "score": 1.0},  # rank 2 -> 1/62
    ]
    fused = rrf_fuse(dense, sparse, _ROUTE_KEY, k=60)
    assert len(fused) == 3
    assert fused[0]["operation_id"] == "op2"
    assert fused[1]["operation_id"] == "op1"
    assert fused[2]["operation_id"] == "op3"


def test_rrf_fuse_disjoint_id_key():
    """A plain scalar id key (used by every non-route collection) works
    identically — this is the whole point of generalizing key_fn."""
    dense = [{"id": 1, "score": 0.9}, {"id": 2, "score": 0.8}]
    sparse = [{"id": 3, "score": 1.5}, {"id": 4, "score": 1.1}]
    fused = rrf_fuse(dense, sparse, _ID_KEY, k=60)
    assert len(fused) == 4
    assert {c["id"] for c in fused} == {1, 2, 3, 4}


def test_rrf_fuse_overlapping_id_key():
    dense = [{"id": 1, "score": 0.9}, {"id": 2, "score": 0.8}]
    sparse = [{"id": 2, "score": 2.0}, {"id": 3, "score": 1.0}]
    fused = rrf_fuse(dense, sparse, _ID_KEY, k=60)
    assert len(fused) == 3
    assert fused[0]["id"] == 2  # present in both lists at rank 1 -> highest fused score


def test_rrf_fuse_empty_sparse():
    dense = [{"id": 1, "score": 0.9}, {"id": 2, "score": 0.8}]
    fused = rrf_fuse(dense, [], _ID_KEY, k=60)
    assert len(fused) == 2
    assert fused[0]["score"] == 1.0 / 61
    assert fused[1]["score"] == 1.0 / 62


def test_normalize_rrf_scores():
    """Raw RRF magnitudes map to [0,1]; ordering preserved. Same contract as
    before generalization — key_fn doesn't affect normalization math."""
    k = 60
    both = [{"id": 1, "score": 2.0 / 61}]
    norm_both = normalize_rrf_scores([dict(c) for c in both], k=k, n_lists=2)
    assert norm_both[0]["score"] == pytest.approx(1.0)

    single = [{"id": 1, "score": 1.0 / 61}, {"id": 2, "score": 1.0 / 62}]
    norm_single = normalize_rrf_scores([dict(c) for c in single], k=k, n_lists=2)
    assert norm_single[0]["score"] == pytest.approx(0.5)
    assert norm_single[1]["score"] < 0.5

    one_list = [{"id": 1, "score": 1.0 / 61}]
    norm_one = normalize_rrf_scores([dict(c) for c in one_list], k=k, n_lists=1)
    assert norm_one[0]["score"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# search() — dense/hybrid routing, per-collection override
# ---------------------------------------------------------------------------

_SPEC_WITH_SPARSE = SearchSpec(
    name="test-collection",
    model=object,
    select_cols=(),
    embedding_col=None,
    model_col=None,
    search_doc_col=object(),  # non-None -> hybrid-capable
    key_fn=_ID_KEY,
    row_to_dict=lambda row: dict(row),
)

_SPEC_DENSE_ONLY = SearchSpec(
    name="test-collection-dense-only",
    model=object,
    select_cols=(),
    embedding_col=None,
    model_col=None,
    search_doc_col=None,  # no tsvector column -> dense-only regardless of config
    key_fn=_ID_KEY,
    row_to_dict=lambda row: dict(row),
)

_SEL = {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536}


@pytest.mark.asyncio
async def test_search_dense_only_when_hybrid_disabled():
    with patch("db_layer.embeddings.search_engine.embed_query_with", new_callable=AsyncMock) as mock_embed, \
         patch("utils.server_config.HYBRID_SEARCH_ENABLED", False), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", False), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}), \
         patch("db_layer.embeddings.search_engine._dense", new_callable=AsyncMock) as mock_dense, \
         patch("db_layer.embeddings.search_engine._sparse", new_callable=AsyncMock) as mock_sparse:
        mock_embed.return_value = [0.1, 0.2]
        mock_dense.return_value = [{"id": 1, "score": 0.9}]

        res = await search(_SPEC_WITH_SPARSE, "test query", _SEL, top_k=1)
        assert res == [{"id": 1, "score": 0.9}]
        mock_dense.assert_called_once()
        mock_sparse.assert_not_called()


@pytest.mark.asyncio
async def test_search_dense_only_when_no_search_doc_col():
    """A table with no tsvector column stays dense-only even if hybrid is
    globally enabled — this is what makes rollout per-table safe (adding a
    provider/table never accidentally activates unbuilt sparse search)."""
    with patch("db_layer.embeddings.search_engine.embed_query_with", new_callable=AsyncMock) as mock_embed, \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", True), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}), \
         patch("db_layer.embeddings.search_engine._dense", new_callable=AsyncMock) as mock_dense, \
         patch("db_layer.embeddings.search_engine._sparse", new_callable=AsyncMock) as mock_sparse:
        mock_embed.return_value = [0.1, 0.2]
        mock_dense.return_value = [{"id": 1, "score": 0.9}]

        res = await search(_SPEC_DENSE_ONLY, "test query", _SEL, top_k=1)
        assert res == [{"id": 1, "score": 0.9}]
        mock_dense.assert_called_once()
        mock_sparse.assert_not_called()


@pytest.mark.asyncio
async def test_search_hybrid_fuses_and_normalizes():
    dense = [{"id": 1, "score": 0.9}, {"id": 2, "score": 0.8}]
    sparse = [{"id": 1, "score": 2.0}, {"id": 3, "score": 1.0}]
    with patch("db_layer.embeddings.search_engine.embed_query_with", new_callable=AsyncMock) as mock_embed, \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", True), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}), \
         patch("db_layer.embeddings.search_engine.HYBRID_DENSE_TOP_N", 10), \
         patch("db_layer.embeddings.search_engine.HYBRID_SPARSE_TOP_N", 10), \
         patch("db_layer.embeddings.search_engine.HYBRID_RRF_K", 60), \
         patch("db_layer.embeddings.search_engine.HYBRID_FUSED_TOP_N", 25), \
         patch("db_layer.embeddings.search_engine._dense", new_callable=AsyncMock) as mock_dense, \
         patch("db_layer.embeddings.search_engine._sparse", new_callable=AsyncMock) as mock_sparse:
        mock_embed.return_value = [0.1, 0.2]
        mock_dense.return_value = dense
        mock_sparse.return_value = sparse

        res = await search(_SPEC_WITH_SPARSE, "test query", _SEL, top_k=5)
        assert res[0]["id"] == 1  # rank-1 in both lists
        assert 0.5 < res[0]["score"] <= 1.0 + 1e-9
        mock_dense.assert_called_once()
        mock_sparse.assert_called_once()


@pytest.mark.asyncio
async def test_search_per_collection_override_disables_hybrid():
    """Global flag on, but this collection is explicitly opted out."""
    with patch("db_layer.embeddings.search_engine.embed_query_with", new_callable=AsyncMock) as mock_embed, \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", True), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {"test-collection": False}), \
         patch("db_layer.embeddings.search_engine._dense", new_callable=AsyncMock) as mock_dense, \
         patch("db_layer.embeddings.search_engine._sparse", new_callable=AsyncMock) as mock_sparse:
        mock_embed.return_value = [0.1, 0.2]
        mock_dense.return_value = [{"id": 1, "score": 0.9}]

        res = await search(_SPEC_WITH_SPARSE, "test query", _SEL, top_k=1)
        assert res == [{"id": 1, "score": 0.9}]
        mock_dense.assert_called_once()
        mock_sparse.assert_not_called()


@pytest.mark.asyncio
async def test_search_per_collection_override_enables_hybrid():
    """Global flag off, but this collection is explicitly opted in."""
    dense = [{"id": 1, "score": 0.9}]
    sparse = [{"id": 1, "score": 2.0}]
    with patch("db_layer.embeddings.search_engine.embed_query_with", new_callable=AsyncMock) as mock_embed, \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", False), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {"test-collection": True}), \
         patch("db_layer.embeddings.search_engine._dense", new_callable=AsyncMock) as mock_dense, \
         patch("db_layer.embeddings.search_engine._sparse", new_callable=AsyncMock) as mock_sparse:
        mock_embed.return_value = [0.1, 0.2]
        mock_dense.return_value = dense
        mock_sparse.return_value = sparse

        res = await search(_SPEC_WITH_SPARSE, "test query", _SEL, top_k=1)
        assert res[0]["id"] == 1
        mock_dense.assert_called_once()
        mock_sparse.assert_called_once()


@pytest.mark.asyncio
async def test_search_applies_row_filter_and_extra_filters_passthrough():
    """extra_filters/row_filter are threaded to _dense (smoke test that the
    engine doesn't drop them)."""
    with patch("db_layer.embeddings.search_engine.embed_query_with", new_callable=AsyncMock) as mock_embed, \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_ENABLED", False), \
         patch("db_layer.embeddings.search_engine.HYBRID_SEARCH_COLLECTIONS", {}), \
         patch("db_layer.embeddings.search_engine._dense", new_callable=AsyncMock) as mock_dense:
        mock_embed.return_value = [0.1, 0.2]
        mock_dense.return_value = []

        extra = lambda stmt: stmt  # noqa: E731
        row_filter = lambda cand: True  # noqa: E731
        await search(_SPEC_DENSE_ONLY, "q", _SEL, top_k=3, extra_filters=extra, row_filter=row_filter)
        args, _kwargs = mock_dense.call_args
        # _dense(spec, vector, top_k, model_id, extra_filters, row_filter)
        assert args[4] is extra
        assert args[5] is row_filter


# ---------------------------------------------------------------------------
# _dense over-fetch: denylist (row_filter) robustness (Work Item 2)
# ---------------------------------------------------------------------------

from db_layer.embeddings.search_engine import (  # noqa: E402
    _dense,
    _DENSE_OVERFETCH_FACTOR,
    _DENSE_OVERFETCH_CAP,
)


@pytest.mark.asyncio
async def test_dense_denylist_overfetch_fills_top_k():
    """Dense-only + heavy denylist still returns up to top_k.

    Simulates a row pool where 3 out of 4*top_k rows survive the denylist.
    Without over-fetching, the SQL LIMIT == top_k would give only top_k raw
    rows, of which most would be filtered — resulting in fewer than top_k
    results.  With over-fetching (FACTOR=4) the pool is large enough.
    """
    top_k = 3
    # Deny rows with id < 10 — survivors are ids 10,11,12 (3 total)
    # The over-fetched pool (4*3=12 rows) contains ids 0..11
    denied_ids = set(range(10))  # ids 0-9 are denied
    row_filter = lambda d: d["id"] not in denied_ids  # noqa: E731

    # Build a fake DB result: 12 rows (ids 0..11), ordered best-first
    fake_rows = [
        type("Row", (), {"id": i, "similarity": 1.0 - i * 0.05})()
        for i in range(top_k * _DENSE_OVERFETCH_FACTOR)
    ]

    from unittest.mock import MagicMock

    # Use MagicMock columns so cosine_distance/is_not/== don't raise on None
    mock_embed_col = MagicMock()
    mock_model_col = MagicMock()

    # row_to_dict extracts id and similarity is injected by _dense itself
    spec = SearchSpec(
        name="test-denylist",
        model=object,
        select_cols=(),
        embedding_col=mock_embed_col,
        model_col=mock_model_col,
        search_doc_col=None,
        key_fn=lambda c: c["id"],
        row_to_dict=lambda row: {"id": row.id},
    )

    async def fake_session_execute(stmt):
        class FakeResult:
            def all(self_):
                return fake_rows
        return FakeResult()

    class FakeSession:
        async def execute(self, stmt):
            return await fake_session_execute(stmt)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    import contextlib
    from unittest.mock import MagicMock

    @contextlib.asynccontextmanager
    async def fake_get_async_session():
        yield FakeSession()

    # Build a chainable mock statement so None column access doesn't crash
    fake_stmt = MagicMock()
    fake_stmt.where.return_value = fake_stmt
    fake_stmt.order_by.return_value = fake_stmt
    fake_stmt.limit.return_value = fake_stmt
    mock_select = MagicMock(return_value=fake_stmt)

    with patch("db_layer.embeddings.search_engine.get_async_session", fake_get_async_session), \
         patch("db_layer.embeddings.search_engine.select", mock_select):
        result = await _dense(spec, [0.1], top_k, "m", None, row_filter)

    surviving_ids = [r["id"] for r in result]
    # ids 10 and 11 survive the denylist; id 12 would be survivor but not in pool
    assert set(surviving_ids) == {10, 11}, f"expected {{10, 11}}, got {surviving_ids}"
    # Without overfetch (limit=3): ids 0,1,2 fetched → all denied → empty result
    # With overfetch (limit=12): ids 0..11 fetched → 10,11 survive
    assert len(result) == 2  # only 2 non-denied rows in pool of 12

    # Verify that the SQL LIMIT was set to the over-fetch value, not top_k
    limit_calls = [call[0][0] for call in fake_stmt.limit.call_args_list]
    assert _DENSE_OVERFETCH_FACTOR * top_k in limit_calls, (
        f"Expected over-fetch limit {_DENSE_OVERFETCH_FACTOR * top_k}, got {limit_calls}"
    )


@pytest.mark.asyncio
async def test_dense_no_row_filter_uses_exact_limit():
    """When row_filter is None the SQL LIMIT stays at top_k (no over-fetch)
    so the change has zero impact on unfiltered paths."""
    top_k = 5
    captured_limit: list[int] = []

    from unittest.mock import MagicMock
    import contextlib

    # Use MagicMock columns so cosine_distance/is_not/== don't raise on None
    mock_embed_col = MagicMock()
    mock_model_col = MagicMock()

    spec = SearchSpec(
        name="test-exact",
        model=object,
        select_cols=(),
        embedding_col=mock_embed_col,
        model_col=mock_model_col,
        search_doc_col=None,
        key_fn=lambda c: c["id"],
        row_to_dict=lambda row: {"id": row.id},
    )

    fake_stmt = MagicMock()
    fake_stmt.where.return_value = fake_stmt
    fake_stmt.order_by.return_value = fake_stmt

    def _capture_limit(n):
        captured_limit.append(n)
        return fake_stmt

    fake_stmt.limit.side_effect = _capture_limit
    mock_select = MagicMock(return_value=fake_stmt)

    class FakeResult:
        def all(self):
            return []

    class FakeSession:
        async def execute(self, stmt):
            return FakeResult()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    @contextlib.asynccontextmanager
    async def fake_get_async_session():
        yield FakeSession()

    with patch("db_layer.embeddings.search_engine.get_async_session", fake_get_async_session), \
         patch("db_layer.embeddings.search_engine.select", mock_select):
        await _dense(spec, [0.1], top_k, "m", None, None)

    assert captured_limit == [top_k], (
        f"Expected SQL LIMIT={top_k} with no row_filter, got {captured_limit}"
    )
