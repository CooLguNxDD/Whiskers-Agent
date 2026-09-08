"""MTU-4 contract: streaming cleans up its gatherer task on early exit.

``stream_graph_impl`` spawns a background ``_gatherer`` task that fans out the
LangGraph event stream into a queue. Before this fix the task was only awaited
on the happy path; if the SSE consumer disconnected early (GeneratorExit) the
gatherer kept running, draining the model stream into an orphaned queue forever.
It must now be cancelled+awaited in a finally block.

Also: ``invalidate_graph`` must NOT silently drop the live ``_pg_pool`` (that
leaked the checkpointer pool on every LLM-pool swap); the pool is reusable
across graph rebuilds and is only closed by ``shutdown_checkpointer``.
"""

import asyncio

import pytest

import core_graph.mcp_tool as mt


class _FakeStream:
    def __init__(self, cancelled: asyncio.Event):
        self._cancelled = cancelled

    def __aiter__(self):
        return self._protocol_iter()

    async def _protocol_iter(self):
        try:
            while True:
                await asyncio.sleep(3600)
                yield {"method": "updates", "params": {"data": {}}}  # pragma: no cover
        except asyncio.CancelledError:
            self._cancelled.set()
            raise

    @property
    def values(self):
        cancelled = self._cancelled

        async def gen():
            yield {"first": True}
            try:
                await asyncio.sleep(3600)  # block until cancelled
            except asyncio.CancelledError:
                cancelled.set()
                raise
            yield {"unreachable": True}

        return gen()

    @property
    def messages(self):
        async def gen():
            await asyncio.sleep(3600)
            yield  # pragma: no cover

        return gen()


class _FakeGraph:
    def __init__(self, cancelled: asyncio.Event):
        self._cancelled = cancelled

    async def astream_events(self, initial_state, version=None, config=None):
        return _FakeStream(self._cancelled)


@pytest.mark.asyncio
async def test_gatherer_cancelled_on_early_consumer_exit(monkeypatch):
    monkeypatch.setattr(mt, "_LLM_USABLE", True, raising=False)
    monkeypatch.setattr(mt, "_DB_AVAILABLE", True, raising=False)
    cancelled = asyncio.Event()

    async def _fake_get_graph():
        return _FakeGraph(cancelled)

    monkeypatch.setattr(mt, "_get_graph", _fake_get_graph)

    agen = mt.stream_graph_impl("hello")
    first = await agen.__anext__()
    assert first["type"] == "values"

    # Simulate client disconnect mid-stream.
    await agen.aclose()

    # The gatherer (and its consumers) must be cancelled promptly.
    await asyncio.wait_for(cancelled.wait(), timeout=2.0)


def test_invalidate_graph_preserves_pool(monkeypatch):
    pool = object()
    saver = object()
    monkeypatch.setattr(mt, "_pg_pool", pool, raising=False)
    monkeypatch.setattr(mt, "_pg_saver", saver, raising=False)
    monkeypatch.setattr(mt, "_compiled_graph", object(), raising=False)

    mt.invalidate_graph()

    assert mt._compiled_graph is None       # graph dropped for rebuild
    assert mt._pg_pool is pool               # pool preserved, not leaked
    assert mt._pg_saver is saver
