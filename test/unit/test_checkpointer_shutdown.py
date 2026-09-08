"""MTU-1 contract: server shutdown releases the Postgres checkpointer pool.

The dynamic graph lazily opens an AsyncConnectionPool (max_size=10) for the
LangGraph Postgres checkpointer and stores it in module globals. Before this
change nothing closed it on teardown, leaking up to 10 PG connections per
process for the process lifetime. ``shutdown_checkpointer()`` must close the
pool and reset the module globals so a subsequent run rebuilds cleanly.
"""

import asyncio

import pytest

import core_graph.mcp_tool as mt


class _FakePool:
    """Stand-in for AsyncConnectionPool tracking close() invocation."""

    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_shutdown_closes_pool_and_resets_globals(monkeypatch):
    pool = _FakePool()
    monkeypatch.setattr(mt, "_pg_pool", pool, raising=False)
    monkeypatch.setattr(mt, "_pg_saver", object(), raising=False)
    monkeypatch.setattr(mt, "_compiled_graph", object(), raising=False)

    await mt.shutdown_checkpointer()

    assert pool.closed is True
    assert mt._pg_pool is None
    assert mt._pg_saver is None
    assert mt._compiled_graph is None


@pytest.mark.asyncio
async def test_shutdown_is_noop_when_no_pool(monkeypatch):
    # Nothing initialised yet -> must not raise.
    monkeypatch.setattr(mt, "_pg_pool", None, raising=False)
    monkeypatch.setattr(mt, "_pg_saver", None, raising=False)
    await mt.shutdown_checkpointer()
    assert mt._pg_pool is None


@pytest.mark.asyncio
async def test_shutdown_swallows_close_errors(monkeypatch):
    class _BoomPool:
        async def close(self):
            raise RuntimeError("pool already closed")

    monkeypatch.setattr(mt, "_pg_pool", _BoomPool(), raising=False)
    monkeypatch.setattr(mt, "_pg_saver", object(), raising=False)
    # Must not propagate; globals still reset.
    await mt.shutdown_checkpointer()
    assert mt._pg_pool is None
    assert mt._pg_saver is None


@pytest.mark.asyncio
async def test_run_teardown_invokes_checkpointer_shutdown(monkeypatch):
    """run_teardown must call shutdown_checkpointer when DB is available."""
    import core.bootstrap as bootstrap

    called = {"shutdown": False}

    async def _fake_shutdown():
        called["shutdown"] = True

    monkeypatch.setattr(mt, "shutdown_checkpointer", _fake_shutdown, raising=False)

    # Minimal fake BootContext: db available, registry attrs absent/None.
    class _Reg:
        lifecycle = None
        _relay = None
        auth = None
        events = None

        class _LC:
            async def teardown_plugins(self):
                return None

        def __init__(self):
            self.lifecycle = self._LC()

    class _Ctx:
        db_available = True

        def __init__(self):
            self.registry = _Reg()

    # Neutralise the other DB-gated teardown steps so the test stays isolated.
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("core_graph.worker.stop_worker", _noop, raising=False)
    monkeypatch.setattr("db_layer.connection.dispose_async_engine", _noop, raising=False)

    await bootstrap.run_teardown(_Ctx())
    assert called["shutdown"] is True
