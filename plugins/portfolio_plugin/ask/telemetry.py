"""Ask observability: aggregate telemetry + the durable per-turn record.

Mirrors ``bake/telemetry.py`` exactly. ``collector.record_tool_call`` is an
in-memory, minute-bucketed aggregate feeding the analytics WS stream; it
cannot answer "who asked what about the fish tank," so every ask turn also
writes a ``portfolio_ask_turns`` row.

Ask stays ephemeral in every other sense — this module never persists the
overlay blocks, the DAG, or the generated answer text. Only the question
(truncated), intent, and outcome are durable.

Nothing here may fail an ask turn: both writes are individually swallowed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.ask.telemetry")

_TOOL_NAME = "portfolio_ask"
_PLUGIN_ID = "portfolio_plugin"


def new_run_id() -> str:
    """Opaque id correlating an ask response with its turn row."""
    return uuid.uuid4().hex


async def record_ask_turn(
    *,
    run_id: str,
    tenant_id: int,
    question: str,
    latency_ms: int,
    ok: bool,
    subject: str | None = None,
    visitor_session_id: str | None = None,
    intent: str | None = None,
    view: str | None = None,
    focus_slug: str | None = None,
    highlight_slugs: list[str] | None = None,
    add_slugs: list[str] | None = None,
    error_type: str | None = None,
    parent_run_id: str | None = None,
) -> None:
    """Emit aggregate telemetry and persist the durable ask-turn record."""
    try:
        from core.telemetry import collector

        collector.record_tool_call(
            tool=_TOOL_NAME,
            plugin_id=_PLUGIN_ID,
            model=None,
            latency_ms=latency_ms,
            ok=ok,
            error_type=error_type,
            subject=subject,
            feature="mcp",
            parent_run_id=parent_run_id,
        )
    except Exception as exc:
        logger.debug("telemetry record_tool_call failed: %s", exc)

    try:
        from plugins.portfolio_plugin.store import create_ask_turn

        await create_ask_turn(
            run_id=run_id,
            tenant_id=tenant_id,
            question=question,
            subject=subject,
            visitor_session_id=visitor_session_id,
            intent=intent,
            view=view,
            focus_slug=focus_slug,
            highlight_slugs=highlight_slugs,
            add_slugs=add_slugs,
            ok=ok,
            error_type=error_type,
            latency_ms=latency_ms,
            parent_run_id=parent_run_id,
        )
    except Exception as exc:
        logger.warning("ask turn record failed: %s", exc)


async def mark_ask_turn_failed(*, run_id: str, error_type: str | None = None) -> None:
    """Flip an already-recorded turn to failed when overlay build errors after route_ask succeeded.

    One row per run_id — never a second write (idempotent error path).
    """
    try:
        from plugins.portfolio_plugin.store import mark_ask_turn_failed as _mark

        await _mark(run_id=run_id, error_type=error_type)
    except Exception as exc:
        logger.warning("ask turn error-mark failed for run_id=%s: %s", run_id, exc)
