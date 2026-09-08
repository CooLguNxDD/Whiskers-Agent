"""Shared multi-stack graph runtime: bootstrap, registry, mode router, retriage."""

from core_graph.runtime.bootstrap import (
    evict_ephemeral_thread,
    get_checkpointer,
    get_compiled_graph,
    invalidate_graph,
    session_lock,
    shutdown_checkpointer,
    thread_config,
)
from core_graph.runtime.mode_router import (
    RunRequest,
    RunResult,
    ensure_default_stacks,
    graph_mode,
    oneshot_can_handle,
    run as run_routed,
    select_kind,
    select_stack,
)
from core_graph.runtime.registry import StackSpec, get_stack, list_stacks, register_stack
from core_graph.runtime.retriage import build_retriage_reset, should_retriage

# Register default stacks on import
ensure_default_stacks()

__all__ = [
    "RunRequest",
    "RunResult",
    "StackSpec",
    "build_retriage_reset",
    "ensure_default_stacks",
    "evict_ephemeral_thread",
    "get_checkpointer",
    "get_compiled_graph",
    "get_stack",
    "graph_mode",
    "invalidate_graph",
    "list_stacks",
    "oneshot_can_handle",
    "register_stack",
    "run_routed",
    "select_kind",
    "select_stack",
    "session_lock",
    "should_retriage",
    "shutdown_checkpointer",
    "thread_config",
]
