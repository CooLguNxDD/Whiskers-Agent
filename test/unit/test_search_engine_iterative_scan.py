"""HNSW iterative scan for tenant/model-filtered dense search (search_engine._dense).

Network-free: a fake session records executed statements and models how an HNSW
index scan interacts with a post-filter (``hnsw.ef_search`` candidates, then WHERE),
so the multi-tenant recall regression runs without a real Postgres/pgvector.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql.elements import TextClause

from db_layer.embeddings import search_engine
from db_layer.embeddings.search_engine import SearchSpec, search_by_vector

VEC = [0.1, 0.2, 0.3]


@pytest.fixture(autouse=True)
def _reset_model_roles_state():
    # Override test/unit/conftest.py's autouse fixture: it imports core_graph, which is
    # irrelevant here and not importable on a bare host interpreter.
    yield


@pytest.fixture(autouse=True)
def _reset_warned():
    search_engine._iterative_scan_warned = False
    yield
    search_engine._iterative_scan_warned = False


Base = declarative_base()


class Doc(Base):
    __tablename__ = "iter_scan_docs"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, nullable=False)
    model_id = Column(String, nullable=False)
    embedding = Column(Vector(3))


SPEC = SearchSpec(
    name="iter-scan-test",
    model=Doc,
    select_cols=(Doc.id, Doc.tenant_id),
    embedding_col=Doc.embedding,
    model_col=Doc.model_id,
    key_fn=lambda d: d["id"],
    row_to_dict=lambda r: {"id": r.id, "tenant_id": r.tenant_id},
)

EF_SEARCH = 3  # tiny stand-in for hnsw.ef_search (default 40)


def _tenant_filter(tenant_id: int):
    return lambda stmt: stmt.where(Doc.tenant_id == tenant_id)


class _Nested:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        self._s.log.append("SAVEPOINT")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._s.log.append("ROLLBACK TO SAVEPOINT" if exc_type else "RELEASE SAVEPOINT")
        return False  # propagate, like SQLAlchemy


class FakeSession:
    """Records statements; the SELECT emulates an HNSW scan followed by the WHERE filter.

    ``index`` is the global nearest-first list of (id, tenant_id, similarity). Without the
    iterative setting only the first EF_SEARCH index entries are considered before the
    tenant filter; with it the scan continues until ``limit`` rows survive.
    """

    def __init__(self, index, *, fail_set: bool = False):
        self.index = index
        self.fail_set = fail_set
        self.log: list[str] = []
        self.executed: list[object] = []
        self.iterative = False

    def begin_nested(self):
        return _Nested(self)

    async def execute(self, stmt, params=None):
        self.executed.append(stmt)
        if isinstance(stmt, TextClause):
            self.log.append("SET")
            if self.fail_set:
                raise RuntimeError('unrecognized configuration parameter "hnsw.iterative_scan"')
            assert "set_config" in stmt.text and "hnsw.iterative_scan" in stmt.text
            assert stmt.text.count(", true)") == 2  # is_local=true: SET LOCAL semantics
            assert params["mode"] == "relaxed_order"
            assert "hnsw.max_scan_tuples" in stmt.text and int(params["max_tuples"]) > 0
            self.iterative = True
            return SimpleNamespace()
        self.log.append("SELECT")
        compiled = stmt.compile()
        tenant = next(v for k, v in compiled.params.items() if k.startswith("tenant_id"))
        limit = stmt._limit
        pool = self.index if self.iterative else self.index[:EF_SEARCH]
        rows = [
            SimpleNamespace(id=i, tenant_id=t, similarity=sim)
            for i, t, sim in pool
            if t == tenant
        ][:limit]
        return SimpleNamespace(all=lambda: rows)


def _patch_session(session: FakeSession):
    @asynccontextmanager
    async def _ctx():
        yield session

    return patch("db_layer.embeddings.search_engine.get_async_session", _ctx)


def _skewed_index():
    # Tenant 1 owns the 10 nearest vectors; tenant 2's 2 rows come after.
    rows = [(i, 1, 0.99 - i * 0.01) for i in range(10)]
    rows += [(100, 2, 0.5), (101, 2, 0.4)]
    return rows


@pytest.mark.asyncio
async def test_set_runs_before_select_on_same_session():
    s = FakeSession(_skewed_index())
    with _patch_session(s):
        await search_by_vector(SPEC, VEC, 5, "m", _tenant_filter(2))
    assert s.log == ["SAVEPOINT", "SET", "RELEASE SAVEPOINT", "SELECT"]


@pytest.mark.asyncio
async def test_tenant_and_model_filters_still_applied():
    s = FakeSession(_skewed_index())
    with _patch_session(s):
        rows = await search_by_vector(SPEC, VEC, 5, "m", _tenant_filter(2))
    sql = str(s.executed[-1].compile())
    assert "tenant_id" in sql and "model_id" in sql and "IS NOT NULL" in sql
    assert {r["tenant_id"] for r in rows} == {2}


@pytest.mark.asyncio
async def test_multi_tenant_recall_needs_iterative_scan():
    # Without iterative scan (SET unavailable) the small tenant gets 0 rows: the first
    # EF_SEARCH candidates all belong to tenant 1 and the tenant filter drops them.
    plain = FakeSession(_skewed_index(), fail_set=True)
    with _patch_session(plain):
        assert await search_by_vector(SPEC, VEC, 5, "m", _tenant_filter(2)) == []

    s = FakeSession(_skewed_index())
    with _patch_session(s):
        rows = await search_by_vector(SPEC, VEC, 5, "m", _tenant_filter(2))
    assert [r["id"] for r in rows] == [100, 101]


@pytest.mark.asyncio
async def test_failing_set_falls_back_to_plain_query(caplog):
    s = FakeSession(_skewed_index(), fail_set=True)
    with _patch_session(s), caplog.at_level("WARNING"):
        rows = await search_by_vector(SPEC, VEC, 3, "m", _tenant_filter(1))
        await search_by_vector(SPEC, VEC, 3, "m", _tenant_filter(1))
    # SAVEPOINT rolled back, SELECT still ran and returned rows
    assert s.log[:4] == ["SAVEPOINT", "SET", "ROLLBACK TO SAVEPOINT", "SELECT"]
    assert [r["id"] for r in rows] == [0, 1, 2]
    # warned once, not per query
    assert sum("iterative_scan unavailable" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_dense_leg_of_hybrid_search_enables_iterative_scan():
    s = FakeSession(_skewed_index())
    with _patch_session(s):
        rows = await search_engine._dense(SPEC, VEC, 5, "m", _tenant_filter(2), None)
    assert s.log == ["SAVEPOINT", "SET", "RELEASE SAVEPOINT", "SELECT"]
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_relaxed_order_rows_are_resorted_best_first():
    class Reversed(FakeSession):
        async def execute(self, stmt, params=None):
            res = await super().execute(stmt, params)
            if hasattr(res, "all"):
                rows = res.all()
                return SimpleNamespace(all=lambda: list(reversed(rows)))
            return res

    s = Reversed(_skewed_index())
    with _patch_session(s):
        rows = await search_by_vector(SPEC, VEC, 5, "m", _tenant_filter(2))
    assert [r["id"] for r in rows] == [100, 101]
