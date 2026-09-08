"""Shared abstractions for the server boot pipeline."""
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

@dataclass
class BootContext:
    """Shared state passed between boot phases."""
    registry: Any
    mcp: Any
    db_available: bool
    plan: Any = None
    # Optional startup-banner coroutine, injected by the entrypoint to avoid
    # re-importing whiskers_agent_mcp (which would re-run _set_registry and
    # clobber the populated registry singleton with a fresh empty one).
    log_startup: Callable[[], Awaitable[None]] | None = None


@dataclass
class Phase:
    """A distinct step in the startup sequence."""
    name: str
    run: Callable[[BootContext], Awaitable[None]]
    fatal: bool
