"""checkpoint_sweeper backstop: sweep stale ``ephemeral-`` threads that the
clean-exit ``evict_ephemeral_thread`` path missed (abort / timeout / restart)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import core_graph.runtime.bootstrap as bootstrap


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        self.executed = (sql, params)
        return None

    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.last_cursor = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def cursor(self):
        self.last_cursor = _FakeCursor(self._rows)
        return self.last_cursor


class _FakePool:
    def __init__(self, rows):
        self._rows = rows
        self.last_conn = None

    def connection(self):
        self.last_conn = _FakeConn(self._rows)
        return self.last_conn


class _FakeSaver:
    def __init__(self):
        self.deleted = []

    async def adelete_thread(self, thread_id):
        self.deleted.append(thread_id)


@pytest.mark.asyncio
async def test_sweep_deletes_stale_ephemeral_threads(monkeypatch):
    saver = _FakeSaver()
    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock(return_value=saver))
    monkeypatch.setattr(
        bootstrap, "_pg_pool",
        _FakePool([{"thread_id": "ephemeral-a"}, {"thread_id": "ephemeral-b"}]),
    )

    swept = await bootstrap.sweep_stale_ephemeral_threads(older_than_hours=24)

    assert swept == 2
    assert sorted(saver.deleted) == ["ephemeral-a", "ephemeral-b"]


@pytest.mark.asyncio
async def test_sweep_noop_when_no_checkpointer(monkeypatch):
    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock(return_value=None))
    swept = await bootstrap.sweep_stale_ephemeral_threads()
    assert swept == 0


@pytest.mark.asyncio
async def test_sweep_swallows_query_errors(monkeypatch):
    saver = _FakeSaver()
    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock(return_value=saver))

    class _BoomPool:
        def connection(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(bootstrap, "_pg_pool", _BoomPool())
    swept = await bootstrap.sweep_stale_ephemeral_threads()
    assert swept == 0


@pytest.mark.asyncio
async def test_sweep_continues_past_one_delete_failure(monkeypatch):
    class _PartialFailSaver:
        def __init__(self):
            self.deleted = []

        async def adelete_thread(self, thread_id):
            if thread_id == "ephemeral-bad":
                raise RuntimeError("locked")
            self.deleted.append(thread_id)

    saver = _PartialFailSaver()
    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock(return_value=saver))
    monkeypatch.setattr(
        bootstrap, "_pg_pool",
        _FakePool([{"thread_id": "ephemeral-bad"}, {"thread_id": "ephemeral-ok"}]),
    )

    swept = await bootstrap.sweep_stale_ephemeral_threads()
    assert swept == 1
    assert saver.deleted == ["ephemeral-ok"]


@pytest.mark.asyncio
async def test_sweep_query_includes_batch_limit(monkeypatch):
    saver = _FakeSaver()
    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock(return_value=saver))
    pool = _FakePool([])
    monkeypatch.setattr(bootstrap, "_pg_pool", pool)

    await bootstrap.sweep_stale_ephemeral_threads(older_than_hours=24)

    sql, params = pool.last_conn.last_cursor.executed
    assert "LIMIT" in sql
    assert params == (24, bootstrap._SWEEP_BATCH_LIMIT)


@pytest.mark.asyncio
async def test_sweep_yields_every_n_deletes(monkeypatch):
    """A batch larger than _SWEEP_YIELD_EVERY must still delete every row in the
    batch (the yield is cooperative scheduling, not an early cutoff) while
    actually calling asyncio.sleep(0) at the yield points."""
    saver = _FakeSaver()
    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock(return_value=saver))
    rows = [{"thread_id": f"ephemeral-{i}"} for i in range(bootstrap._SWEEP_YIELD_EVERY + 5)]
    monkeypatch.setattr(bootstrap, "_pg_pool", _FakePool(rows))

    sleep_calls = {"n": 0}
    real_sleep = bootstrap.asyncio.sleep

    async def counting_sleep(delay):
        sleep_calls["n"] += 1
        await real_sleep(delay)

    monkeypatch.setattr(bootstrap.asyncio, "sleep", counting_sleep)

    swept = await bootstrap.sweep_stale_ephemeral_threads()

    assert swept == len(rows)
    assert sleep_calls["n"] >= 1
