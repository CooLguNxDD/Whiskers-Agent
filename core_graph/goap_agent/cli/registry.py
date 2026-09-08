"""Driver name → instance registry."""

from __future__ import annotations

import os

from core_graph.goap_agent.cli.agy import AgyCliDriver
from core_graph.goap_agent.cli.claude import ClaudeCliDriver
from core_graph.goap_agent.cli.generic import GenericCliDriver
from core_graph.goap_agent.cli.grok import GrokCliDriver

_DRIVERS = {
    "claude": ClaudeCliDriver(),
    "agy": AgyCliDriver(),
    "grok": GrokCliDriver(),
    "generic": GenericCliDriver(),
}


def list_drivers() -> list[str]:
    """Return registered driver names."""
    return sorted(_DRIVERS.keys())


def get_driver(name: str | None = None):
    """Resolve a driver by name, or from CLI_AGENT_PROVIDER / LLM_PROVIDER."""
    if not name:
        name = (os.environ.get("CLI_AGENT_PROVIDER") or "").strip().lower()
    if not name:
        # Map LLM provider ids to drivers
        llm = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
        if llm in ("claude-cli", "claude"):
            name = "claude"
        elif llm in ("agy-cli", "agy", "antigravity"):
            name = "agy"
        elif llm in ("grok-cli", "grok"):
            name = "grok"
        else:
            name = "claude"
    key = name.strip().lower()
    # Accept LLM provider aliases
    aliases = {
        "claude-cli": "claude",
        "agy-cli": "agy",
        "antigravity": "agy",
        "grok-cli": "grok",
    }
    key = aliases.get(key, key)
    driver = _DRIVERS.get(key)
    if driver is None:
        raise KeyError(f"Unknown CLI agent driver '{name}'. Available: {list_drivers()}")
    return driver
