"""Cached checkpoint gauges and sweeper telemetry."""

import pytest

from core.telemetry.collector import collector
import core_graph.runtime.bootstrap as bootstrap
import core_graph.worker.checkpoint_sweeper as sweeper


def test_gauges_are_cached_and_registered_without_database(monkeypatch):
    monkeypatch.setitem(sweeper._COUNTS, "blobs", 17)
    sweeper._register_gauges()
    try:
        assert collector.snapshot()["checkpoint_rows_blobs"] == 17
        assert all(isinstance(provider(), int) for provider in sweeper._GAUGES.values())
    finally:
        sweeper._unregister_gauges()
    assert "checkpoint_rows_blobs" not in collector.snapshot()


@pytest.mark.asyncio
async def test_tick_updates_table_and_deletion_counts(monkeypatch):
    async def counts():
        return {"checkpoints": 10, "checkpoint_blobs": 20, "checkpoint_writes": 30}

    async def sweep(**kwargs):
        return 3

    monkeypatch.setattr("core_graph.runtime.bootstrap.checkpoint_table_counts", counts)
    monkeypatch.setattr("core_graph.runtime.bootstrap.sweep_stale_ephemeral_threads", sweep)
    monkeypatch.setattr(sweeper, "_stale_hours", lambda: 24)
    for key in ("checkpoints", "blobs", "writes"):
        monkeypatch.setitem(sweeper._COUNTS, key, sweeper._COUNTS[key])
    monkeypatch.setitem(sweeper._LAST_DELETED, "deleted", sweeper._LAST_DELETED["deleted"])
    before = sweeper._TOTAL_DELETED
    monkeypatch.setattr(sweeper, "_TOTAL_DELETED", before)
    await sweeper._tick()
    assert sweeper._COUNTS == {"checkpoints": 10, "blobs": 20, "writes": 30}
    assert sweeper._LAST_DELETED["deleted"] == 3
    assert sweeper._TOTAL_DELETED == before + 3


@pytest.mark.asyncio
async def test_checkpoint_table_counts_uses_catalog_estimates(monkeypatch):
    from unittest.mock import AsyncMock

    class Cursor:
        def __init__(self, results):
            self.results, self.sql = iter(results), []
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def execute(self, sql, params=None): self.sql.append((sql, params))
        async def fetchone(self): return next(self.results)

    cursor = Cursor([{"row_count": 11}, (22,), {"row_count": 33}])
    class Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def cursor(self): return cursor
    class Pool:
        def connection(self): return Conn()

    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock())
    monkeypatch.setattr(bootstrap, "_pg_pool", Pool())
    assert await bootstrap.checkpoint_table_counts() == {
        "checkpoints": 11, "checkpoint_blobs": 22, "checkpoint_writes": 33
    }
    assert len(cursor.sql) == 3
    assert all("reltuples" in sql and params == (table,) for (sql, params), table in zip(
        cursor.sql, ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
    ))


@pytest.mark.asyncio
async def test_checkpoint_table_counts_clamps_unanalyzed_and_missing_tables(monkeypatch):
    from unittest.mock import AsyncMock

    class Cursor:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def execute(self, sql, params=None):
            assert "GREATEST(reltuples, 0)" in sql
        async def fetchone(self): return None

    class Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def cursor(self): return Cursor()

    class Pool:
        def connection(self): return Conn()

    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock())
    monkeypatch.setattr(bootstrap, "_pg_pool", Pool())
    # reltuples=-1 is clamped by SQL; a missing table has no catalog row.
    # Both represent an estimate of zero to telemetry.
    assert await bootstrap.checkpoint_table_counts() == {
        "checkpoints": 0, "checkpoint_blobs": 0, "checkpoint_writes": 0
    }


@pytest.mark.asyncio
async def test_checkpoint_table_counts_requires_pool(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock())
    monkeypatch.setattr(bootstrap, "_pg_pool", None)
    with pytest.raises(RuntimeError, match="pool is unavailable"):
        await bootstrap.checkpoint_table_counts()


@pytest.mark.asyncio
async def test_count_failure_preserves_cached_counts(monkeypatch):
    old = dict(sweeper._COUNTS)

    async def counts():
        raise RuntimeError("db unavailable")

    async def sweep(**kwargs):
        return 0

    monkeypatch.setattr("core_graph.runtime.bootstrap.checkpoint_table_counts", counts)
    monkeypatch.setattr("core_graph.runtime.bootstrap.sweep_stale_ephemeral_threads", sweep)
    await sweeper._tick()
    assert sweeper._COUNTS == old
