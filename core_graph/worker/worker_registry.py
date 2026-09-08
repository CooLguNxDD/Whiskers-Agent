"""
Worker registry utilities.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class WorkerSpec:
    """
    Specification for a registered worker.
    """
    name: str
    run: Callable[..., Any]
    stop: Callable[[], Awaitable[None]]
    enabled_check: Optional[Callable[[], bool]] = None
    task: Optional[asyncio.Task] = field(default=None, init=False)

class WorkerRegistry:
    """
    Registry for managing worker specs.
    """
    def __init__(self):
        """Initialize the WorkerRegistry with empty worker specs and running workers maps."""
        self._workers: List[WorkerSpec] = []
        self._running_workers: Dict[str, WorkerSpec] = {}

    def register(self, spec: WorkerSpec, *, replace: bool = False) -> None:
        """Register a WorkerSpec, ignoring a duplicate name unless ``replace`` is set.

        Registration is idempotent by default: a second ``register()`` for the same
        name used to append a shadowed duplicate that ``start()``'s lookup picked
        arbitrarily, which is why callers hand-rolled their own dedupe scans.
        ``replace=True`` is for hot-reload, where the new spec must win.
        """
        if not isinstance(spec, WorkerSpec):
            raise TypeError("Only WorkerSpec instances can be registered.")
        existing = self.get(spec.name)
        if existing is not None:
            if not replace:
                logger.warning("Worker '%s' is already registered; ignoring duplicate.", spec.name)
                return
            self._workers.remove(existing)
        self._workers.append(spec)

    def ensure_registered(self, spec: WorkerSpec) -> bool:
        """Register ``spec`` only if its name is absent. True when newly added."""
        if self.is_registered(spec.name):
            return False
        self.register(spec)
        return True

    def get(self, name: str) -> Optional[WorkerSpec]:
        """Return the registered spec for ``name``, or None."""
        return next((w for w in self._workers if w.name == name), None)

    def is_registered(self, name: str) -> bool:
        """Whether a worker is registered under ``name``."""
        return self.get(name) is not None

    def is_running(self, name: str) -> bool:
        """Whether ``name`` was started and not yet stopped.

        Deliberately sticky: this tracks registry bookkeeping, not task liveness,
        so it stays True if the worker's task died on its own.
        """
        return name in self._running_workers

    def names(self) -> List[str]:
        """Names of all registered workers, in registration order."""
        return [w.name for w in self._workers]

    def running_names(self) -> List[str]:
        """Names of all workers currently marked running, in start order."""
        return list(self._running_workers.keys())

    def start(self, name: str) -> Optional[asyncio.Task]:
        """Start a specific worker by name if not already running."""
        spec = self.get(name)
        if not spec:
            logger.warning(f"Worker '{name}' not found in registry.")
            return None

        if spec.name in self._running_workers:
            logger.info(f"Worker '{name}' is already running.")
            return self._running_workers[spec.name].task

        if spec.enabled_check and not spec.enabled_check():
            logger.info(f"Worker '{name}' is disabled by configuration.")
            return None

        logger.info(f"Starting worker: {name}")
        try:
            # Run the worker and store the task
            task_result = spec.run()
            if isinstance(task_result, asyncio.Task):
                spec.task = task_result
            else:
                # If run() is not an async function that returns a Task,
                # we assume it starts its own background process/thread and doesn't return a task
                spec.task = None
            self._running_workers[name] = spec
            return spec.task
        except Exception:
            logger.exception(f"Failed to start worker '{name}'.")
            return None

    def start_all(self) -> None:
        """Start all registered workers."""
        for spec in self._workers:
            self.start(spec.name)

    async def stop(self, name: str) -> None:
        """Stop a running worker by name."""
        spec = self._running_workers.pop(name, None)
        if not spec:
            logger.info(f"Worker '{name}' is not running.")
            return

        logger.info(f"Stopping worker: {name}")
        try:
            await spec.stop()
            logger.info(f"Stopped {name}")
        except Exception:
            logger.exception(f"Failed to stop worker '{name}'.")

    async def stop_all(self) -> None:
        """Stop all currently running workers in reverse order of startup."""
        # Reverse *start* order, not registration order — _running_workers is
        # keyed by start sequence, and unwinding in that order is what we want.
        for spec_name in reversed(list(self._running_workers.keys())):
            await self.stop(spec_name)


_registry: Optional[WorkerRegistry] = None

def get_worker_registry() -> WorkerRegistry:
    """
    Retrieves the active worker registry.
    """
    global _registry
    if _registry is None:
        _registry = WorkerRegistry()
    return _registry

def _set_worker_registry(r: WorkerRegistry) -> None:
    global _registry
    _registry = r
