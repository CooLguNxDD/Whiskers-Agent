"""Cached checkpoint gauges and sweeper telemetry."""

import pytest

import core.telemetry.collector as telemetry
import core_graph.worker.checkpoint_sweeper as sweeper


def test_gauges_are_cached_and_registered_without_database(monkeypatch):
    monkeypatch.setitem(sweeper._COUNTS, "blobs", 17)
    sweeper._register_gauges()
    try:
        assert telemetry.snapshot()["checkpoint_rows_blobs"] == 17
        assert all(isinstance(provider(), int) for provider in sweeper._GAUGES.values())
    finally:
        sweeper._unregister_gauges()
    assert "checkpoint_rows_blobs" not in telemetry.snapshot()


@pytest.mark.asyncio
async def test_tick_updates_table_and_deletion_counts(monkeypatch):
    async def counts():
        return {"checkpoints": 10, "checkpoint_blobs": 20, "checkpoint_writes": 30}

    async def sweep(**kwargs):
        return 3

    monkeypatch.setattr("core_graph.runtime.bootstrap.checkpoint_table_counts", counts)
    monkeypatch.setattr("core_graph.runtime.bootstrap.sweep_stale_ephemeral_threads", sweep)
    monkeypatch.setattr(sweeper, "_stale_hours", lambda: 24)
    before = sweeper._TOTAL_DELETED
    await sweeper._tick()
    assert sweeper._COUNTS == {"checkpoints": 10, "blobs": 20, "writes": 30}
    assert sweeper._LAST_DELETED["deleted"] == 3
    assert sweeper._TOTAL_DELETED == before + 3


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
