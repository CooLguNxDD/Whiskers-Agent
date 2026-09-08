"""Shared environment-flag parsing for the GOAP agent CLI drivers.

Stdlib-only on purpose: ``cli/user_drop.py`` runs during privilege drop, before
the heavier ``core_graph`` imports are wanted, so this module must stay cheap.
"""

import os


def env_truthy(name: str, *, default: bool = False) -> bool:
    """Read an env var as a boolean; unset or blank falls back to ``default``."""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
