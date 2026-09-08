"""Unit tests for BackgroundWorker, the shared polling-worker lifecycle.

Two of these are regression tests for real bugs the five hand-rolled worker
loops had drifted into: a stop-before-first-tick race, and a force-cancel that
never awaited the cancelled task.
"""

import asyncio
import logging

import pytest

from core_graph.worker.worker_loop import BackgroundWorker

logger = logging.getLogger("whiskers.test.worker")


@pytest.mark.asyncio
async def test_stop_before_first_tick_exits_immediately():
    """B2 regression: stop() racing the first loop entry must not wait out the interval."""
    entered = asyncio.Event()

    async def _tick() -> bool:
        entered.set()
        return False

    worker = BackgroundWorker("w", _tick, interval=30.0, logger=logger)
    task = worker.start()
    # No await in between — stop lands before the loop coroutine has run at all.
    await asyncio.wait_for(worker.stop(), timeout=0.5)
    assert task.done()
    assert worker.task is None
    assert worker.stop_event is None


@pytest.mark.asyncio
async def test_hung_tick_is_cancelled_and_awaited():
    """B1 regression: past the stop timeout the task is cancelled *and* awaited."""
    async def _tick() -> bool:
        await asyncio.sleep(60)
        return False

    worker = BackgroundWorker("w", _tick, interval=0.01, logger=logger, stop_timeout=0.1)
    task = worker.start()
    await asyncio.sleep(0.05)
    await worker.stop()
    assert task.done()
    assert task.cancelled() or task.exception() is None
    assert worker.task is None
    assert worker.stop_event is None


@pytest.mark.asyncio
async def test_truthy_tick_skips_idle_sleep():
    """A tick reporting work loops straight into the next one."""
    calls = {"n": 0}

    async def _tick() -> bool:
        calls["n"] += 1
        return calls["n"] < 5

    worker = BackgroundWorker("w", _tick, interval=30.0, logger=logger)
    worker.start()
    await asyncio.sleep(0.05)
    # Five ticks ran back-to-back despite a 30s idle interval; the 5th idles.
    assert calls["n"] == 5
    await worker.stop()


@pytest.mark.asyncio
async def test_falsy_tick_idles_between_iterations():
    """None and False both mean 'no work' — the worker waits out the interval."""
    calls = {"n": 0}

    async def _tick():
        calls["n"] += 1
        return None

    worker = BackgroundWorker("w", _tick, interval=5.0, logger=logger)
    worker.start()
    await asyncio.sleep(0.05)
    assert calls["n"] == 1
    await worker.stop()


@pytest.mark.asyncio
async def test_callable_interval_is_reevaluated_each_iteration():
    """Live config reload: the interval callable runs once per idle wait."""
    seen: list[float] = []

    def _interval() -> float:
        seen.append(0.01)
        return 0.01

    async def _tick() -> bool:
        return False

    worker = BackgroundWorker("w", _tick, interval=_interval, logger=logger)
    worker.start()
    await asyncio.sleep(0.06)
    await worker.stop()
    assert len(seen) >= 2


@pytest.mark.asyncio
async def test_raising_tick_survives_and_reaches_on_error():
    """The loop must outlive a failing tick, handing the exception to on_error."""
    seen: list[BaseException] = []
    calls = {"n": 0}

    async def _tick() -> bool:
        calls["n"] += 1
        raise RuntimeError("boom")

    worker = BackgroundWorker(
        "w", _tick, interval=0.01, logger=logger, on_error=seen.append
    )
    worker.start()
    await asyncio.sleep(0.06)
    await worker.stop()
    assert calls["n"] >= 2
    assert seen and isinstance(seen[0], RuntimeError)


@pytest.mark.asyncio
async def test_stopped_log_emitted_even_when_tick_raises(caplog):
    """The finally-based 'stopped' log must not be skippable by an exception."""
    async def _tick() -> bool:
        raise RuntimeError("boom")

    worker = BackgroundWorker("noisy_worker", _tick, interval=0.01, logger=logger)
    with caplog.at_level(logging.INFO, logger="whiskers.test.worker"):
        worker.start()
        await asyncio.sleep(0.03)
        await worker.stop()
    assert "noisy_worker: stopped" in caplog.text


@pytest.mark.asyncio
async def test_stopped_log_emitted_on_cancel(caplog):
    """Same guarantee when the task is force-cancelled past the stop timeout."""
    async def _tick() -> bool:
        await asyncio.sleep(60)
        return False

    worker = BackgroundWorker(
        "hung_worker", _tick, interval=0.01, logger=logger, stop_timeout=0.05
    )
    with caplog.at_level(logging.INFO, logger="whiskers.test.worker"):
        worker.start()
        await asyncio.sleep(0.02)
        await worker.stop()
    assert "hung_worker: stopped" in caplog.text


@pytest.mark.asyncio
async def test_start_is_idempotent():
    """A second start() while live returns the same task, not a second loop."""
    async def _tick() -> bool:
        return False

    worker = BackgroundWorker("w", _tick, interval=5.0, logger=logger)
    first = worker.start()
    second = worker.start()
    assert first is second
    await worker.stop()


@pytest.mark.asyncio
async def test_stop_without_start_is_noop():
    """Stopping a worker that was never started must not raise."""
    async def _tick() -> bool:
        return False

    worker = BackgroundWorker("w", _tick, interval=5.0, logger=logger)
    await worker.stop()
    assert worker.task is None
    assert worker.running is False


@pytest.mark.asyncio
async def test_tick_kwargs_are_forwarded():
    """start(**kwargs) passes through to every tick call."""
    seen: list[int] = []

    async def _tick(batch_size: int) -> bool:
        seen.append(batch_size)
        return False

    worker = BackgroundWorker("w", _tick, interval=5.0, logger=logger)
    worker.start(batch_size=7)
    await asyncio.sleep(0.02)
    await worker.stop()
    assert seen == [7]


@pytest.mark.asyncio
async def test_should_stop_visible_to_tick():
    """Ticks that iterate internally can poll should_stop() to bail early."""
    observed: list[bool] = []

    async def _tick() -> bool:
        observed.append(worker.should_stop())
        await asyncio.sleep(0.05)
        observed.append(worker.should_stop())
        return False

    worker = BackgroundWorker("w", _tick, interval=5.0, logger=logger)
    worker.start()
    await asyncio.sleep(0.01)
    await worker.stop()
    assert observed[0] is False
    assert observed[-1] is True
