"""Single-process guard — warn when config says the server runs more than one process.

The MCP subscription bus, the rate limiter and the per-session graph locks all keep
their state in process memory, so a second worker/replica silently splits that
state. This module only *detects and warns*; it never raises and never changes
behaviour. See ``SetupGuide.md`` ("Single-process constraint").
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping

logger = logging.getLogger("whiskers")

# Env vars that conventional ASGI/WSGI launchers read for their worker count.
WORKER_ENV_VARS: tuple[str, ...] = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS")
# Keys of the optional top-level ``deployment`` block in server_config.json.
DEPLOYMENT_KEYS: tuple[str, ...] = ("replicas", "workers")

SINGLE_PROCESS_COMPONENTS = (
    "MCP subscription bus (subscriptions/listen)",
    "rate limiter",
    "graph session locks",
)
DOC_POINTER = "SetupGuide.md#-single-process-constraint"


def _as_count(value: Any) -> int | None:
    """Parse a worker/replica count; None for anything that is not a plain integer."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def detect_multi_process_config(
    env: Mapping[str, str] | None = None,
    server_config: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return one human-readable reason per signal that implies more than one process.

    Pure and tolerant: unset, empty, non-integer and <= 1 values are ignored.
    """
    reasons: list[str] = []
    env = env if env is not None else {}
    for name in WORKER_ENV_VARS:
        count = _as_count(env.get(name))
        if count is not None and count > 1:
            reasons.append(f"env {name}={count}")

    deployment = server_config.get("deployment") if isinstance(server_config, Mapping) else None
    if isinstance(deployment, Mapping):
        for key in DEPLOYMENT_KEYS:
            count = _as_count(deployment.get(key))
            if count is not None and count > 1:
                reasons.append(f"server_config deployment.{key}={count}")
    return reasons


def warn_single_process_only(reasons: list[str]) -> None:
    """Log one warning per reason, naming the affected components and the doc section."""
    for reason in reasons:
        logger.warning(
            "Multi-process deployment detected (%s) but these components are "
            "single-process only and will not share state across workers/replicas: %s. "
            "Run one worker, or use sticky sessions plus proxy-level rate limiting. See %s",
            reason,
            ", ".join(SINGLE_PROCESS_COMPONENTS),
            DOC_POINTER,
        )


def warn_if_multi_process(
    env: Mapping[str, str] | None = None,
    server_config: Mapping[str, Any] | None = None,
) -> list[str]:
    """Detect and warn in one call; returns the reasons. Never raises.

    Defaults to ``os.environ`` / the loaded server config.
    """
    try:
        if env is None:
            env = os.environ
        if server_config is None:
            from utils.server_config import SERVER_CONFIG

            server_config = SERVER_CONFIG
        reasons = detect_multi_process_config(env, server_config)
        warn_single_process_only(reasons)
        return reasons
    except Exception as exc:  # startup diagnostics must never block boot
        logger.debug("single-process guard skipped: %s", exc)
        return []
