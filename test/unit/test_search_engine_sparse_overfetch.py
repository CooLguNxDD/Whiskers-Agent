"""Over-fetch of the sparse (FTS) leg when a Python-side row_filter is set.

Network-free: a fake session captures the compiled statement's LIMIT and
emulates it over an in-memory result set, so the denylist scenario exercises
the real ``_sparse`` / ``_dense`` code paths. Imports stay stdlib + SQLAlchemy +
pgvector only, hence the conftest override below.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.dialects.postgresql import TSVECTOR

from db_layer.embeddings import search_engine as se


@pytest.fixture(autouse=True)
def _reset_model_roles_state():
    """Override the conftest autouse fixture: nothing here needs core_graph."""
    yield


_T = Table(
    "fake_docs",
    MetaData(),
    Column("id", Integer, primary_key=True),
    Column("model", String),
    Column("embedding", Vector(3)),
    Column("search_doc", TSVECTOR),
)

SPEC = se.SearchSpec(
    name="fake",
    model=object,
    select_cols=[_T.c.id],
    embedding_col=_T.c.embedding,
    model_col=_T.c.model,
    key_fn=lambda d: d["id"],
    row_to_dict=lambda row: {"id": row.id},
    search_doc_col=_T.c.search_doc,
)


class _FakeSession:
    """Captures statements; returns the first ``LIMIT`` rows of ``rows``."""

    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        limit = stmt._limit_clause.value if stmt._limit_clause is not None else None
        data = self._rows if limit is None else self._rows[:limit]
        return SimpleNamespace(all=lambda: list(data))

    @property
    def limit(self):
        return self.statements[-1]._limit_clause.value


def _rows(n):
    return [SimpleNamespace(id=i, similarity=1.0 - i / (n + 1)) for i in range(n)]


def _patched(session):
    @asynccontextmanager
    async def _cm():
        yield session

    return patch.object(se, "get_async_session", _cm)


async def _run_sparse(session, top_k, row_filter):
    with _patched(session):
        return await se._sparse(SPEC, "query", top_k, "m", None, row_filter)


async def _run_dense(session, top_k, row_filter):
    with _patched(session):
        return await se._dense(SPEC, [0.1, 0.2, 0.3], top_k, "m", None, row_filter)


@pytest.mark.asyncio
async def test_sparse_limit_is_top_k_without_row_filter():
    session = _FakeSession(_rows(100))
    res = await _run_sparse(session, 10, None)
    assert session.limit == 10
    assert len(res) == 10


@pytest.mark.asyncio
async def test_sparse_limit_is_overfetched_with_row_filter():
    session = _FakeSession(_rows(100))
    await _run_sparse(session, 10, lambda d: True)
    assert session.limit == min(10 * se._DENSE_OVERFETCH_FACTOR, se._DENSE_OVERFETCH_CAP)


@pytest.mark.asyncio
async def test_sparse_denylist_heavy_results_still_reach_top_k():
    top_k = 5
    denied = set(range(top_k))  # the first top_k ranked rows are all denied
    session = _FakeSession(_rows(50))
    res = await _run_sparse(session, top_k, lambda d: d["id"] not in denied)
    assert [d["id"] for d in res] == list(range(top_k, 2 * top_k))
    assert all(d["id"] not in denied for d in res)


@pytest.mark.asyncio
async def test_sparse_trims_to_top_k_after_filter():
    session = _FakeSession(_rows(100))
    res = await _run_sparse(session, 5, lambda d: True)
    assert len(res) == 5
    assert [d["id"] for d in res] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_sparse_limit_respects_cap_for_large_top_k():
    session = _FakeSession(_rows(10))
    top_k = se._DENSE_OVERFETCH_CAP  # top_k * factor would blow past the cap
    await _run_sparse(session, top_k, lambda d: True)
    assert session.limit == se._DENSE_OVERFETCH_CAP


@pytest.mark.asyncio
async def test_sparse_without_search_doc_col_returns_empty():
    spec = se.SearchSpec(**{**SPEC.__dict__, "search_doc_col": None})
    session = _FakeSession(_rows(3))
    with _patched(session):
        assert await se._sparse(spec, "q", 3, "m", None, lambda d: True) == []
    assert session.statements == []


@pytest.mark.asyncio
async def test_dense_behaviour_unchanged():
    # no filter: LIMIT == top_k
    session = _FakeSession(_rows(100))
    res = await _run_dense(session, 10, None)
    assert session.limit == 10
    assert len(res) == 10

    # filter: over-fetched, not trimmed by _dense itself (callers trim)
    session = _FakeSession(_rows(100))
    res = await _run_dense(session, 5, lambda d: d["id"] >= 5)
    assert session.limit == min(5 * se._DENSE_OVERFETCH_FACTOR, se._DENSE_OVERFETCH_CAP)
    assert [d["id"] for d in res] == list(range(5, session.limit))


def test_generic_aliases_and_helper_share_constants():
    assert se.OVERFETCH_FACTOR == se._DENSE_OVERFETCH_FACTOR
    assert se.OVERFETCH_CAP == se._DENSE_OVERFETCH_CAP
    assert se._sql_limit(7, None) == 7
    assert se._sql_limit(7, lambda d: True) == min(7 * se._DENSE_OVERFETCH_FACTOR, se._DENSE_OVERFETCH_CAP)
