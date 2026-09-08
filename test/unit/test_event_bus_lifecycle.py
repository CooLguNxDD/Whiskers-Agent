"""Unit tests for PluginEventBus task tracking, deregistration, and lifecycle."""

import asyncio
import pytest
from core.plugin_loader.plugin_event_bus import PluginEventBus


@pytest.mark.asyncio
async def test_emit_stores_task():
    """Verify that emit() registers async tasks in the _tasks set."""
    bus = PluginEventBus()
    event_called = asyncio.Event()

    async def async_handler():
        await event_called.wait()

    bus.on("test_event", async_handler)
    bus.emit("test_event")

    # The task should be in the _tasks set immediately
    assert len(bus._tasks) == 1
    # Clean up by signaling the event and awaiting the tasks
    event_called.set()
    await asyncio.gather(*bus._tasks)


@pytest.mark.asyncio
async def test_task_auto_removed():
    """Verify that a task is automatically removed from _tasks after completion."""
    bus = PluginEventBus()
    event_called = asyncio.Event()

    async def async_handler():
        event_called.set()

    bus.on("test_event", async_handler)
    bus.emit("test_event")

    assert len(bus._tasks) == 1
    # Allow the event loop to run the task
    await event_called.wait()
    # Yield control to let the done callback execute
    await asyncio.sleep(0)
    assert len(bus._tasks) == 0


def test_off_prevents_handler():
    """Verify off() prevents handler from firing on subsequent emit() calls."""
    bus = PluginEventBus()
    calls = []

    def sync_handler():
        calls.append(1)

    bus.on("test_event", sync_handler)
    bus.emit("test_event")
    assert len(calls) == 1

    bus.off("test_event", sync_handler)
    bus.emit("test_event")
    assert len(calls) == 1


def test_off_missing_noop():
    """Verify off() is a silent no-op when event or handler is missing."""
    bus = PluginEventBus()

    def handler():
        pass

    # Missing event - should not raise exception
    bus.off("missing_event", handler)

    # Existing event, but handler not registered - should not raise exception
    bus.on("existing_event", lambda: None)
    bus.off("existing_event", handler)


@pytest.mark.asyncio
async def test_emit_async_awaits_handlers_in_order():
    """emit_async runs sync handlers inline and awaits async handlers sequentially."""
    bus = PluginEventBus()
    order = []

    async def async_first():
        order.append(1)
        await asyncio.sleep(0.01)

    def sync_second():
        order.append(2)

    async def async_third():
        order.append(3)

    bus.on("ordered", async_first)
    bus.on("ordered", sync_second)
    bus.on("ordered", async_third)

    await bus.emit_async("ordered")
    assert order == [1, 2, 3]


@pytest.mark.asyncio
async def test_emit_async_completes_before_return():
    """emit_async side-effects are visible immediately after await (unlike fire-and-forget emit)."""
    bus = PluginEventBus()
    done = False

    async def slow_handler():
        nonlocal done
        await asyncio.sleep(0.02)
        done = True

    bus.on("slow", slow_handler)
    await bus.emit_async("slow")
    assert done is True


@pytest.mark.asyncio
async def test_emit_fire_and_forget_does_not_block():
    """emit dispatches async handlers without awaiting them."""
    bus = PluginEventBus()
    done = False

    async def slow_handler():
        nonlocal done
        await asyncio.sleep(0.05)
        done = True

    bus.on("ff", slow_handler)
    bus.emit("ff")
    assert done is False
    await asyncio.sleep(0.1)
    assert done is True


@pytest.mark.asyncio
async def test_drain_cancels_and_clears():
    """Verify drain() cancels running tasks and clears the tasks set."""
    bus = PluginEventBus()
    task_cancelled = False

    async def long_running():
        nonlocal task_cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            task_cancelled = True
            raise

    bus.on("test_event", long_running)
    bus.emit("test_event")
    assert len(bus._tasks) == 1

    # Yield control to the event loop to let the task start running
    await asyncio.sleep(0.01)

    await bus.drain()
    assert len(bus._tasks) == 0
    assert task_cancelled is True
