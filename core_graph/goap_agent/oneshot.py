"""Back-compat shim — oneshot lives under ``core_graph.subgraphs.oneshot_cli``."""

from __future__ import annotations

from core_graph.subgraphs.oneshot_cli.mode import run_cli_oneshot, stream_cli_oneshot
from core_graph.subgraphs.oneshot_cli.policy import (
    RECURSION_GUARD_SOURCE,
    active_core_cli_provider,
    agent_for_provider as _agent_for_provider,
    oneshot_enabled,
)

__all__ = [
    "RECURSION_GUARD_SOURCE",
    "active_core_cli_provider",
    "oneshot_enabled",
    "run_cli_oneshot",
    "stream_cli_oneshot",
    "_agent_for_provider",
]
