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


# ---------------------------------------------------------------------------
# Session-thread retention sweep (opt-in, never touches in-use conversations)
# ---------------------------------------------------------------------------


class _SessionSaver:
    """Saver double: records deletes; ``aget_tuple`` exposes pending writes per thread."""

    def __init__(self, pending=None, fail_get=()):
        self.deleted = []
        self._pending = pending or {}
        self._fail_get = set(fail_get)

    async def adelete_thread(self, thread_id):
        self.deleted.append(thread_id)

    async def aget_tuple(self, config):
        thread_id = config["configurable"]["thread_id"]
        if thread_id in self._fail_get:
            raise RuntimeError("read failed")
        writes = self._pending.get(thread_id)
        if writes is None:
            return None
        return MagicMock(pending_writes=writes)


def _patch_session_sweep(monkeypatch, rows, saver=None, locks=None):
    saver = saver or _SessionSaver()
    pool = _FakePool(rows)
    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock(return_value=saver))
    monkeypatch.setattr(bootstrap, "_pg_pool", pool)
    monkeypatch.setattr(bootstrap, "_session_locks", locks if locks is not None else {})
    return saver, pool


@pytest.mark.asyncio
async def test_session_sweep_deletes_stale_session_thread(monkeypatch):
    saver, _ = _patch_session_sweep(monkeypatch, [{"thread_id": "sess-old"}])

    swept = await bootstrap.sweep_stale_session_threads(retention_days=30)

    assert swept == 1
    assert saver.deleted == ["sess-old"]


@pytest.mark.asyncio
async def test_session_sweep_keeps_fresh_session_thread(monkeypatch):
    """Freshness is decided in SQL (HAVING MAX(ts) < cutoff): a fresh thread is
    never returned by the query, so it is never deleted."""
    saver, pool = _patch_session_sweep(monkeypatch, [])

    swept = await bootstrap.sweep_stale_session_threads(retention_days=30)

    assert swept == 0
    assert saver.deleted == []
    sql, params = pool.last_conn.last_cursor.executed
    assert "HAVING MAX((checkpoint->>'ts')::timestamptz)" in sql
    assert params == (30, bootstrap._SWEEP_BATCH_LIMIT)


@pytest.mark.asyncio
async def test_session_sweep_sql_excludes_ephemeral_threads(monkeypatch):
    _, pool = _patch_session_sweep(monkeypatch, [])

    await bootstrap.sweep_stale_session_threads(retention_days=7)

    sql, _params = pool.last_conn.last_cursor.executed
    assert "NOT LIKE 'ephemeral-%%'" in sql
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_session_sweep_does_not_touch_ephemeral_rows_even_if_returned(monkeypatch):
    """Defence in depth: an ephemeral id slipping past the query is still skipped."""
    saver, _ = _patch_session_sweep(
        monkeypatch, [{"thread_id": "ephemeral-x"}, {"thread_id": "sess-old"}]
    )

    swept = await bootstrap.sweep_stale_session_threads(retention_days=30)

    assert swept == 1
    assert saver.deleted == ["sess-old"]


@pytest.mark.asyncio
async def test_session_sweep_skips_thread_with_pending_interrupt(monkeypatch):
    saver = _SessionSaver(
        pending={
            "sess-paused": [("task-1", "__interrupt__", {"value": "approve?"})],
            "sess-idle": [("task-2", "messages", [])],
        }
    )
    _patch_session_sweep(
        monkeypatch,
        [{"thread_id": "sess-paused"}, {"thread_id": "sess-idle"}],
        saver=saver,
    )

    swept = await bootstrap.sweep_stale_session_threads(retention_days=30)

    assert swept == 1
    assert saver.deleted == ["sess-idle"]


@pytest.mark.asyncio
async def test_session_sweep_skips_thread_when_interrupt_check_fails(monkeypatch):
    """Cannot prove the thread is not paused -> leave it for the next tick."""
    saver = _SessionSaver(fail_get={"sess-flaky"})
    _patch_session_sweep(monkeypatch, [{"thread_id": "sess-flaky"}], saver=saver)

    swept = await bootstrap.sweep_stale_session_threads(retention_days=30)

    assert swept == 0
    assert saver.deleted == []


@pytest.mark.asyncio
async def test_session_sweep_skips_thread_with_held_session_lock(monkeypatch):
    import asyncio

    held = asyncio.Lock()
    await held.acquire()
    free = asyncio.Lock()
    saver, _ = _patch_session_sweep(
        monkeypatch,
        [{"thread_id": "sess-busy"}, {"thread_id": "sess-free"}],
        locks={"sess-busy": held, "sess-free": free},
    )

    swept = await bootstrap.sweep_stale_session_threads(retention_days=30)

    assert swept == 1
    assert saver.deleted == ["sess-free"]


@pytest.mark.asyncio
async def test_session_sweep_does_not_create_lock_entries(monkeypatch):
    locks: dict = {}
    _patch_session_sweep(monkeypatch, [{"thread_id": "sess-old"}], locks=locks)

    await bootstrap.sweep_stale_session_threads(retention_days=30)

    assert locks == {}


@pytest.mark.asyncio
async def test_session_sweep_noop_for_non_positive_retention(monkeypatch):
    saver, pool = _patch_session_sweep(monkeypatch, [{"thread_id": "sess-old"}])

    assert await bootstrap.sweep_stale_session_threads(retention_days=0) == 0
    assert await bootstrap.sweep_stale_session_threads(retention_days=-3) == 0

    assert pool.last_conn is None
    assert saver.deleted == []


@pytest.mark.asyncio
async def test_session_sweep_noop_when_no_checkpointer(monkeypatch):
    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock(return_value=None))
    assert await bootstrap.sweep_stale_session_threads(retention_days=30) == 0


@pytest.mark.asyncio
async def test_session_sweep_swallows_query_errors(monkeypatch):
    class _BoomPool:
        def connection(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(bootstrap, "get_checkpointer", AsyncMock(return_value=_SessionSaver()))
    monkeypatch.setattr(bootstrap, "_pg_pool", _BoomPool())
    assert await bootstrap.sweep_stale_session_threads(retention_days=30) == 0


# --- worker wiring ---------------------------------------------------------


def _patch_scheduled(monkeypatch, cfg):
    import utils.server_config as server_config

    monkeypatch.setattr(server_config, "SCHEDULED_JOBS_CONFIG", {"checkpoint_sweep": cfg})


def test_session_retention_days_defaults_to_disabled(monkeypatch):
    import core_graph.worker.checkpoint_sweeper as cs

    _patch_scheduled(monkeypatch, {})
    assert cs._session_retention_days() == 0.0
    _patch_scheduled(monkeypatch, {"session_retention_days": "junk"})
    assert cs._session_retention_days() == 0.0
    _patch_scheduled(monkeypatch, {"session_retention_days": 14})
    assert cs._session_retention_days() == 14.0


@pytest.mark.asyncio
async def test_tick_runs_no_session_sweep_when_retention_zero(monkeypatch):
    import core_graph.worker.checkpoint_sweeper as cs

    _patch_scheduled(monkeypatch, {"session_retention_days": 0})
    eph = AsyncMock(return_value=0)
    sess = AsyncMock(return_value=0)
    monkeypatch.setattr(bootstrap, "sweep_stale_ephemeral_threads", eph)
    monkeypatch.setattr(bootstrap, "sweep_stale_session_threads", sess)

    assert await cs._tick() is False

    eph.assert_awaited_once()
    sess.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_runs_session_sweep_after_ephemeral(monkeypatch):
    import core_graph.worker.checkpoint_sweeper as cs

    _patch_scheduled(monkeypatch, {"session_retention_days": 30})
    order = []

    async def eph(**kw):
        order.append("eph")
        return 0

    async def sess(**kw):
        order.append(("sess", kw["retention_days"]))
        return 0

    monkeypatch.setattr(bootstrap, "sweep_stale_ephemeral_threads", eph)
    monkeypatch.setattr(bootstrap, "sweep_stale_session_threads", sess)

    await cs._tick()

    assert order == ["eph", ("sess", 30.0)]


@pytest.mark.asyncio
async def test_tick_failures_do_not_block_each_other(monkeypatch):
    import core_graph.worker.checkpoint_sweeper as cs

    _patch_scheduled(monkeypatch, {"session_retention_days": 30})
    sess = AsyncMock(return_value=0)
    monkeypatch.setattr(
        bootstrap, "sweep_stale_ephemeral_threads", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(bootstrap, "sweep_stale_session_threads", sess)

    assert await cs._tick() is False
    sess.assert_awaited_once()

    eph = AsyncMock(return_value=0)
    monkeypatch.setattr(bootstrap, "sweep_stale_ephemeral_threads", eph)
    monkeypatch.setattr(
        bootstrap, "sweep_stale_session_threads", AsyncMock(side_effect=RuntimeError("boom"))
    )
    assert await cs._tick() is False
    eph.assert_awaited_once()
