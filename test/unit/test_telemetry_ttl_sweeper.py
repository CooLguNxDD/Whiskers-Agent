"""telemetry_ttl_sweeper: retention sweep of graph_run_events (Phase 4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import delete, func, select

from core_graph.worker import telemetry_ttl_sweeper as sweeper
from db_layer.connection import get_async_session
from db_layer.models.telemetry import GraphRunEvent


class _Result:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcounts=(0,)):
        self.executed = []
        self.commits = 0
        self._rowcounts = list(rowcounts)
        self._i = 0

    async def execute(self, stmt):
        self.executed.append(stmt)
        n = self._rowcounts[self._i] if self._i < len(self._rowcounts) else 0
        self._i += 1
        return _Result(n)

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def committed(self):
        return self.commits > 0


@pytest.mark.asyncio
async def test_tick_deletes_and_commits():
    fake = _FakeSession(rowcounts=(0,))
    with patch("core_graph.worker.telemetry_ttl_sweeper.get_async_session", return_value=fake):
        result = await sweeper._tick()

    assert result is False
    assert len(fake.executed) == 1
    assert fake.commits == 1


@pytest.mark.asyncio
async def test_tick_returns_true_when_a_batch_deleted():
    fake = _FakeSession(rowcounts=(sweeper._BATCH,))
    with patch("core_graph.worker.telemetry_ttl_sweeper.get_async_session", return_value=fake):
        result = await sweeper._tick()

    assert result is True
    assert len(fake.executed) == 1
    assert fake.commits == 1


@pytest.mark.asyncio
async def test_tick_swallows_db_errors():
    class _BoomSession:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *exc):
            return False

    with patch("core_graph.worker.telemetry_ttl_sweeper.get_async_session", return_value=_BoomSession()):
        result = await sweeper._tick()
    assert result is False


def test_config_defaults(monkeypatch):
    monkeypatch.setattr(
        "utils.server_config.SCHEDULED_JOBS_CONFIG", {}, raising=False
    )
    assert sweeper._interval_seconds() == sweeper._DEFAULT_INTERVAL_SECONDS
    assert sweeper._retention_days() == sweeper._DEFAULT_RETENTION_DAYS
    assert sweeper._enabled() is True


def test_config_overrides(monkeypatch):
    monkeypatch.setattr(
        "utils.server_config.SCHEDULED_JOBS_CONFIG",
        {"telemetry_sweep": {"enabled": False, "interval_seconds": 120, "retention_days": 7}},
        raising=False,
    )
    assert sweeper._interval_seconds() == 120
    assert sweeper._retention_days() == 7
    assert sweeper._enabled() is False


def test_register_adds_worker_spec():
    class _FakeRegistry:
        def __init__(self):
            self.specs = []

        def register(self, spec):
            self.specs.append(spec)

    registry = _FakeRegistry()
    sweeper.register(registry)
    assert len(registry.specs) == 1
    assert registry.specs[0].name == "telemetry_ttl_sweeper"


@pytest.mark.asyncio
async def test_tick_chunks_live_backlog(monkeypatch):
    """One tick deletes exactly _BATCH stale rows and returns True until drained."""
    monkeypatch.setattr(sweeper, "_BATCH", 10)
    monkeypatch.setattr(sweeper, "_MAX_BATCHES_PER_TICK", 1)
    prefix = "ttl-chunk-test-"
    stale = datetime.now(timezone.utc) - timedelta(days=40)
    n_insert = 25

    async with get_async_session() as session:
        await session.execute(delete(GraphRunEvent).where(GraphRunEvent.run_id.like(f"{prefix}%")))
        for i in range(n_insert):
            session.add(
                GraphRunEvent(
                    run_id=f"{prefix}{i}",
                    tenant_id=1,
                    ok=True,
                    latency_ms=1,
                    created_at=stale,
                )
            )
        await session.commit()

    try:
        first = await sweeper._tick()
        assert first is True
        async with get_async_session() as session:
            remaining = (
                await session.execute(
                    select(func.count())
                    .select_from(GraphRunEvent)
                    .where(GraphRunEvent.run_id.like(f"{prefix}%"))
                )
            ).scalar_one()
        assert remaining == n_insert - 10

        ticks = 0
        while await sweeper._tick():
            ticks += 1
            assert ticks < 10
        async with get_async_session() as session:
            leftover = (
                await session.execute(
                    select(func.count())
                    .select_from(GraphRunEvent)
                    .where(GraphRunEvent.run_id.like(f"{prefix}%"))
                )
            ).scalar_one()
        assert leftover == 0
    finally:
        async with get_async_session() as session:
            await session.execute(delete(GraphRunEvent).where(GraphRunEvent.run_id.like(f"{prefix}%")))
            await session.commit()
