"""MTU-6 contract: background tasks are awaited after cancellation on shutdown.

Two long-lived tasks were cancelled without being awaited, producing
"Task was destroyed but it is pending" warnings and skipping cleanup:
  * embedding worker — ``stop()`` cancelled the loop on timeout but never
    awaited it.
  * terminal-relay session reaper — ``stop_reaper()`` cancelled the reaper but
    never awaited it (kill hooks mid-reap could be lost).

Both must now await the task (swallowing CancelledError) so it is fully
finalized before the process moves on.
"""

import asyncio

import pytest

import core_graph.worker.embedding_worker as ew
from plugins.cat_terminal_relay_plugin.services.session_registry import SessionRegistry


@pytest.mark.asyncio
async def test_worker_stop_awaits_cooperative_exit(monkeypatch):
    """A tick that honours the stop signal exits cleanly and is awaited."""
    async def _cooperative(**_kwargs):
        await ew._WORKER.sleep(3600)
        return False

    monkeypatch.setattr(ew._WORKER, "_tick", _cooperative, raising=False)
    task = ew.run()
    await asyncio.sleep(0)

    await ew.stop()

    assert task.done()
    assert ew._WORKER.task is None
    assert ew._WORKER.stop_event is None


@pytest.mark.asyncio
async def test_worker_stop_cancels_and_awaits_on_timeout(monkeypatch):
    """A hung tick is force-cancelled past the timeout — and awaited, not orphaned."""
    async def _hung(**_kwargs):
        await asyncio.sleep(3600)
        return False

    monkeypatch.setattr(ew._WORKER, "_tick", _hung, raising=False)
    monkeypatch.setattr(ew._WORKER, "_stop_timeout", 0.1, raising=False)
    task = ew.run()
    await asyncio.sleep(0.01)

    await ew.stop()

    assert task.cancelled() or task.done()
    assert ew._WORKER.task is None
    assert ew._WORKER.stop_event is None


@pytest.mark.asyncio
async def test_stop_reaper_awaits_cancelled_task():
    reg = SessionRegistry()
    reg.start_reaper()
    task = reg._reaper_task
    assert task is not None and not task.done()

    await reg.stop_reaper()

    assert task.done()
    assert reg._reaper_task is None
