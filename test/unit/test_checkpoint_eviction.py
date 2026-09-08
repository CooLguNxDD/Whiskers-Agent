"""MTU-5 contract: one-shot (ephemeral) checkpoint threads are evicted.

The LangGraph Postgres checkpointer writes per-thread state for every run. Runs
without a ``session_id`` get a throwaway ``ephemeral-<uuid>`` thread that has no
cross-turn value, yet its checkpoint rows accumulated forever. After a run the
ephemeral thread must be deleted via ``AsyncPostgresSaver.adelete_thread``;
persisted ``session_id`` threads (e.g. the playground) must be kept.
"""

import pytest

import core_graph.mcp_tool as mt


class _FakeSaver:
    def __init__(self):
        self.deleted = []

    async def adelete_thread(self, thread_id):
        self.deleted.append(thread_id)


class _FakeGraph:
    async def ainvoke(self, state, config=None):
        return {"response": {"status": "ok"}}


@pytest.mark.asyncio
async def test_evict_only_ephemeral(monkeypatch):
    saver = _FakeSaver()
    monkeypatch.setattr(mt, "_pg_saver", saver, raising=False)
    await mt._evict_ephemeral_thread("ephemeral-abc")
    await mt._evict_ephemeral_thread("sess-1")
    assert saver.deleted == ["ephemeral-abc"]


@pytest.mark.asyncio
async def test_evict_no_saver_is_noop(monkeypatch):
    monkeypatch.setattr(mt, "_pg_saver", None, raising=False)
    await mt._evict_ephemeral_thread("ephemeral-x")  # must not raise


@pytest.mark.asyncio
async def test_evict_swallows_errors(monkeypatch):
    class _Boom:
        async def adelete_thread(self, tid):
            raise RuntimeError("db down")

    monkeypatch.setattr(mt, "_pg_saver", _Boom(), raising=False)
    await mt._evict_ephemeral_thread("ephemeral-x")  # must not raise


@pytest.mark.asyncio
async def test_run_graph_evicts_ephemeral_thread(monkeypatch):
    monkeypatch.setattr(mt, "_LLM_USABLE", True, raising=False)
    monkeypatch.setattr(mt, "_DB_AVAILABLE", True, raising=False)
    monkeypatch.setenv("GRAPH_MODE", "root")
    saver = _FakeSaver()
    monkeypatch.setattr(mt, "_pg_saver", saver, raising=False)

    async def _fake_get_graph():
        return _FakeGraph()

    monkeypatch.setattr(mt, "_get_graph", _fake_get_graph)

    out = await mt.run_graph_impl("hi", session_id=None)
    assert out == {"status": "ok"}
    assert len(saver.deleted) == 1
    assert saver.deleted[0].startswith("ephemeral-")


@pytest.mark.asyncio
async def test_run_graph_keeps_persisted_session(monkeypatch):
    monkeypatch.setattr(mt, "_LLM_USABLE", True, raising=False)
    monkeypatch.setattr(mt, "_DB_AVAILABLE", True, raising=False)
    monkeypatch.setenv("GRAPH_MODE", "root")
    saver = _FakeSaver()
    monkeypatch.setattr(mt, "_pg_saver", saver, raising=False)

    async def _fake_get_graph():
        return _FakeGraph()

    monkeypatch.setattr(mt, "_get_graph", _fake_get_graph)

    out = await mt.run_graph_impl("hi", session_id="sess-1")
    assert out == {"status": "ok"}
    assert saver.deleted == []  # persisted session retained
