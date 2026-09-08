"""
Lightweight plugin event bus enabling inter-plugin pub-sub capabilities.
"""
import asyncio
import logging
from typing import Dict, List, Callable

from utils.tasks import spawn_supervised

logger = logging.getLogger("whiskers.plugins")

class PluginEventBus:
    """Lightweight PubSub event bus for cross-plugin communication."""
    def __init__(self):
        """Initialize the event bus with an empty listener dictionary and tasks set."""
        self._listeners: Dict[str, List[Callable]] = {}
        self._tasks: set[asyncio.Task] = set()

    def on(self, event_name: str, handler: Callable):
        """Register a handler for a specific event."""
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        self._listeners[event_name].append(handler)

    def off(self, event_name: str, handler: Callable):
        """Remove a registered handler for an event. Silent noop if not found."""
        if event_name in self._listeners:
            try:
                self._listeners[event_name].remove(handler)
            except ValueError:
                pass

    def _dispatch_handler(self, handler: Callable, *args, **kwargs):
        """Invoke a handler and return its result (coroutine for async handlers)."""
        return handler(*args, **kwargs)

    def emit(self, event_name: str, *args, **kwargs):
        """Trigger all handlers registered for the given event."""
        for handler in self._listeners.get(event_name, []):
            try:
                result = self._dispatch_handler(handler, *args, **kwargs)
                if asyncio.iscoroutine(result):
                    # spawn_supervised logs an unretrieved exception instead of
                    # silently swallowing it (bare create_task did neither).
                    task = spawn_supervised(result, name=f"event_bus:{event_name}")
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
            except Exception as e:
                logger.error(f"Error in event handler for {event_name}: {e}")

    async def emit_async(self, event_name: str, *args, **kwargs):
        """Await all handlers in registration order (sync inline, async sequential)."""
        for handler in self._listeners.get(event_name, []):
            try:
                result = self._dispatch_handler(handler, *args, **kwargs)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Error in event handler for {event_name}: {e}")

    async def drain(self):
        """Cancel and await all pending event tasks."""
        if not self._tasks:
            return
        # Copy the set before iterating since done callbacks may mutate self._tasks
        tasks = set(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

# Alias for backward-compatibility
EventBus = PluginEventBus
