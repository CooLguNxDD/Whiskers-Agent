"""
spawn_supervised — the one place a fire-and-forget asyncio.Task gets created.

Bare ``asyncio.create_task(coro())`` drops its return value, which means:
(1) nothing retrieves the exception if the coroutine raises, so the loop just
logs "Task exception was never retrieved" and the failure is invisible to
whoever needed to know, and (2) nothing holds a strong reference, so the task
can be garbage-collected mid-flight. ``spawn_supervised`` fixes both.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger("whiskers")

_live_tasks: set[asyncio.Task] = set()


def spawn_supervised(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str,
    on_error: Callable[[BaseException], None] | None = None,
) -> asyncio.Task:
    """Create a tracked task that logs (rather than swallows) an unretrieved exception.

    Keeps a strong reference in a module-level set until the task finishes, so it
    cannot be garbage-collected while running. ``on_error`` runs synchronously from
    the done-callback if the coroutine raised (never for cancellation).
    """
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _live_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _live_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is None:
            return
        logger.error("spawn_supervised: task '%s' raised: %s", name, exc, exc_info=exc)
        if on_error is not None:
            try:
                on_error(exc)
            except Exception:
                logger.exception("spawn_supervised: on_error callback for '%s' raised", name)

    task.add_done_callback(_done)
    return task
