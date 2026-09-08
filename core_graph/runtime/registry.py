"""Stack / mode registry for multi-stack graph entry (oneshot, root, specialist)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

# Optional async handlers — oneshot has run/stream; root may only stream via mcp_tool.
RunHandler = Callable[..., Awaitable[Any]]
StreamHandler = Callable[..., Any]  # async generator
CanHandle = Callable[[Any], bool | Awaitable[bool]]


@dataclass(frozen=True)
class StackSpec:
    """One executable graph/mode stack registered with the runtime."""

    id: str
    description: str
    build: Callable[..., Any] | None = None
    run: RunHandler | None = None
    stream: StreamHandler | None = None
    can_handle: CanHandle | None = None
    priority: int = 100  # lower wins when multiple can_handle


_STACKS: dict[str, StackSpec] = {}


def register_stack(spec: StackSpec) -> StackSpec:
    """Register or replace a stack by id. Returns the registered spec."""
    _STACKS[spec.id] = spec
    return spec


def get_stack(stack_id: str) -> StackSpec | None:
    """Return a registered stack or None."""
    return _STACKS.get(stack_id)


def list_stacks() -> list[StackSpec]:
    """Return all registered stacks sorted by priority then id."""
    return sorted(_STACKS.values(), key=lambda s: (s.priority, s.id))


def clear_stacks() -> None:
    """Remove all registrations (tests only)."""
    _STACKS.clear()
