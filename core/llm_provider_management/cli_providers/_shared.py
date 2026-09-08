"""Shared availability-check helper for headless CLI providers.

Moved out of ``core/llm_provider.py``'s ``_cli_binary_available``/``_llm_available``
CLI branch. CLI providers "soft-pass" by default (binary may appear on PATH
later, after this process started) — set ``CLI_AGENT_REQUIRE_BINARY=1`` to
require a real ``shutil.which`` hit instead.
"""

from __future__ import annotations

import os
import shutil
from typing import Callable


def make_cli_is_available(binary_env: str | None, default_binary: str | None) -> Callable[[], bool]:
    """Build an ``is_available`` check for a CLI provider.

    ``binary_env``/``default_binary`` of ``None`` means no binary-presence
    check is implemented for this provider (matches legacy ``grok-cli``
    behavior: always unavailable under ``CLI_AGENT_REQUIRE_BINARY``).
    """

    def _is_available() -> bool:
        require = os.environ.get("CLI_AGENT_REQUIRE_BINARY", "").strip().lower() in ("1", "true", "yes")
        if not require:
            return True
        if not binary_env:
            return False
        binary = (os.environ.get(binary_env) or default_binary or "").strip()
        return bool(binary) and bool(shutil.which(binary))

    return _is_available
