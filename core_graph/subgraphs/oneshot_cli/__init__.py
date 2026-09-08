"""CLI oneshot meta-stack (Mode B) — package entry for multi-stack runtime."""

from core_graph.subgraphs.oneshot_cli.policy import (
    RECURSION_GUARD_SOURCE,
    active_core_cli_provider,
    oneshot_enabled,
)

__all__ = [
    "RECURSION_GUARD_SOURCE",
    "active_core_cli_provider",
    "oneshot_enabled",
]
