"""Bake observability: aggregate telemetry + the durable per-run record.

Two surfaces, deliberately. ``collector.record_tool_call`` is an in-memory,
minute-bucketed aggregate — it answers "is bake healthy right now" and feeds
the analytics WS stream. It cannot answer "why did run ``abc`` ship a floor
layout", so every attempt also writes a ``portfolio_bake_runs`` row.

Nothing here may fail a bake: both writes are individually swallowed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from plugins.portfolio_plugin.bake.run_context import BakeContext

logger = logging.getLogger("whiskers.plugins.portfolio.bake.telemetry")

_TOOL_NAME = "bake_portfolio_for_job"
_PLUGIN_ID = "portfolio_plugin"


def new_run_id() -> str:
    """Opaque id correlating a tool response with its run row and log lines."""
    return uuid.uuid4().hex


def _first_error_code(ctx: BakeContext) -> str | None:
    """Prefer a fatal code as the telemetry error label; else the first seen."""
    for err in ctx.errors:
        if err.fatal:
            return err.code
    return ctx.errors[0].code if ctx.errors else None


async def record_bake_run(
    ctx: BakeContext,
    *,
    run_id: str,
    status: str,
    short_id: str | None = None,
    compose_path: str | None = None,
    mode: str | None = None,
    degraded: bool = False,
    job_brief_hash: str | None = None,
) -> None:
    """Emit aggregate telemetry and persist the durable run record.

    ``status`` is ``ok`` or ``error``. A failed bake still writes a row, with
    ``short_id`` NULL — that row is the only evidence the attempt happened.
    """
    ok = status == "ok"

    try:
        from core.telemetry import collector

        collector.record_tool_call(
            tool=_TOOL_NAME,
            plugin_id=_PLUGIN_ID,
            model=None,
            latency_ms=ctx.total_ms,
            ok=ok,
            error_type=None if ok else (_first_error_code(ctx) or "layout_compose_failed"),
        )
    except Exception as exc:
        logger.debug("telemetry record_tool_call failed: %s", exc)

    try:
        from plugins.portfolio_plugin.store import create_bake_run

        stages: dict[str, Any] = {"timings_ms": ctx.timings()}
        if ctx.provenance:
            stages["provenance"] = ctx.provenance

        await create_bake_run(
            run_id=run_id,
            tenant_id=ctx.tenant_id,
            status=status,
            short_id=short_id,
            compose_path=compose_path,
            mode=mode,
            degraded=degraded,
            company=ctx.company or None,
            role=ctx.role or None,
            job_brief_hash=job_brief_hash,
            evidence_pack_hash=ctx.provenance.get("evidencePackHash"),
            stages=stages,
            errors=ctx.error_dicts(),
            total_ms=ctx.total_ms,
        )
    except Exception as exc:
        logger.warning("bake run record failed: %s", exc)
