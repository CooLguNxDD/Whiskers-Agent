"""BackgroundWorker — the shared lifecycle plumbing behind every polling worker.

Five workers (embedding, enrichment, content_sync, portfolio discovery, world
index) each hand-rolled ``_run_loop``/``run``/``stop`` with the same shape, and
the copies had drifted apart into two real bugs:

* one ``stop()`` cancelled its task without awaiting it, leaking
  "Task was destroyed but it is pending" on every shutdown;
* three created their ``asyncio.Event`` *inside* the loop coroutine, so a
  ``stop()`` landing before the first loop entry signalled an event the loop
  then threw away — the worker only exited via the 10-30s timeout.

Composition, not inheritance: none of the five are classes. Each keeps its thin
module-level ``run()``/``stop()`` functions delegating to a module-level
``BackgroundWorker`` instance, so no call site or dotted-name monkeypatch moves.

Both bugs die by construction here: there is exactly one ``stop()`` (which
always cancels *and* awaits), and ``start()`` creates the stop event on the
caller's stack before ``create_task``, so the loop body owns no event-creation
code at all.

Stdlib imports only — plugins must import this module directly
(``from core_graph.worker.worker_loop import BackgroundWorker``) rather than via
the package, whose ``__init__`` eagerly drags in SQLAlchemy and ``db_layer``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional, Union

# A tick returns True when it did work (loop again immediately), False/None to idle.
TickResult = Optional[bool]
Tick = Callable[..., Awaitable[TickResult]]
Interval = Union[float, Callable[[], float]]


class BackgroundWorker:
    """One polling worker: a tick coroutine, an interval, and a stoppable loop."""

    def __init__(
        self,
        name: str,
        tick: Tick,
        *,
        interval: Interval,
        logger: logging.Logger,
        stop_timeout: float = 10.0,
        on_error: Optional[Callable[[BaseException], None]] = None,
    ) -> None:
        """Configure the worker. Nothing is scheduled until ``start()``.

        ``interval`` may be a callable, re-evaluated every iteration, so a
        worker can pick up live config changes without a restart. ``on_error``
        receives any exception a tick raises; the loop always survives it.
        """
        self.name = name
        self._tick = tick
        self._interval = interval
        self._logger = logger
        self._stop_timeout = stop_timeout
        self._on_error = on_error
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    @property
    def task(self) -> asyncio.Task | None:
        """The running loop task, or None when stopped."""
        return self._task

    @property
    def stop_event(self) -> asyncio.Event | None:
        """The active stop event, or None when stopped."""
        return self._stop_event

    @property
    def running(self) -> bool:
        """Whether a loop task is currently live."""
        return self._task is not None and not self._task.done()

    def should_stop(self) -> bool:
        """True once stop has been signalled — for ticks that iterate internally."""
        return self._stop_event is not None and self._stop_event.is_set()

    def _next_interval(self, override: Interval | None = None) -> float:
        """Resolve this iteration's idle interval, evaluating a callable if given."""
        source = self._interval if override is None else override
        return float(source() if callable(source) else source)

    async def sleep(self, seconds: float | None = None) -> bool:
        """Idle for ``seconds`` (default: the configured interval), interruptibly.

        Returns True if stop was signalled during the wait.
        """
        if self._stop_event is None:
            return True
        timeout = self._next_interval() if seconds is None else float(seconds)
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def start(self, *, interval: Interval | None = None, **tick_kwargs: Any) -> asyncio.Task:
        """Schedule the loop. Idempotent — returns the existing task if live."""
        if self.running:
            assert self._task is not None
            return self._task
        if interval is not None:
            self._interval = interval
        # Created here, on the caller's stack, so a stop() racing the first loop
        # entry signals the event the loop will actually observe.
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(**tick_kwargs), name=self.name)
        return self._task

    async def _loop(self, **tick_kwargs: Any) -> None:
        """Run ticks until stop, idling between them unless a tick did work."""
        stop_event = self._stop_event
        assert stop_event is not None, "start() must create the stop event"
        self._logger.info("%s: started", self.name)
        try:
            while not stop_event.is_set():
                did_work: TickResult = False
                try:
                    did_work = await self._tick(**tick_kwargs)
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001 — the loop must outlive any tick
                    if self._on_error is not None:
                        self._on_error(exc)
                    else:
                        self._logger.exception("%s: tick failed: %s", self.name, exc)
                if did_work:
                    continue
                await self.sleep()
        finally:
            self._logger.info("%s: stopped", self.name)

    async def stop(self) -> None:
        """Signal the loop to exit and await it; force-cancel past the timeout.

        A no-op when the worker was never started.
        """
        if self._stop_event is not None:
            self._stop_event.set()
        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=self._stop_timeout)
            except asyncio.TimeoutError:
                # Cancel *and* await, so the task is fully finalized and never
                # surfaces as "Task was destroyed but it is pending".
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except BaseException:  # noqa: BLE001 — shutdown must not abort on one worker's cleanup error
                    self._logger.exception("%s: error while finalizing cancelled task", self.name)
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None
                self._stop_event = None
        else:
            self._stop_event = None
