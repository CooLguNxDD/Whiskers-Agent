"""Scheduled portfolio discovery worker (default off).

Enabled via ``scheduled_jobs.portfolio_discovery.enabled`` in server_config.json.
"""

from __future__ import annotations

import asyncio
import logging

from core_graph.worker.worker_loop import BackgroundWorker

logger = logging.getLogger("whiskers.plugins.portfolio.discovery.worker")

_IDLE_POLL_SECONDS = 3600.0
_STOP_TIMEOUT = 30.0


def _cfg() -> dict:
    from utils.server_config import SCHEDULED_JOBS_CONFIG

    return SCHEDULED_JOBS_CONFIG.get("portfolio_discovery") or {}


async def _run_once() -> None:
    """One discovery+index cycle per configured tenant (write_back from plugin settings).

    No request principal exists in a scheduled worker — ``tenant_ids`` in
    ``scheduled_jobs.portfolio_discovery`` (default ``[1]``) is the explicit
    boundary config for which tenants get auto-discovery, rather than a
    silent hardcoded ``tenant_id=1``.
    """
    from core.context import current_tenant_id
    from plugins.portfolio_plugin.discovery.pipeline import run_discovery
    from plugins.portfolio_plugin.plugin_config import SETTINGS

    disc = SETTINGS.get("discovery") if isinstance(SETTINGS, dict) else {}
    write_back = bool(disc.get("write_back", False)) if isinstance(disc, dict) else False
    tenant_ids = _cfg().get("tenant_ids") or [1]
    if not isinstance(tenant_ids, list):
        tenant_ids = [tenant_ids]

    for tid in tenant_ids:
        try:
            tid_i = int(tid)
        except (TypeError, ValueError):
            continue
        token = current_tenant_id.set(tid_i)
        try:
            result = await run_discovery(
                scope="all",
                dry_run=False,
                write_back=write_back,
                do_index=True,
                tenant_id=tid_i,
            )
            logger.info(
                "portfolio_discovery_worker: tenant=%s docs=%s indexed=%s write_back=%s",
                tid_i,
                result.get("doc_count"),
                (result.get("index") or {}).get("indexed"),
                result.get("write_back"),
            )
        except Exception:
            logger.exception("portfolio_discovery_worker: tenant=%s cycle failed", tid_i)
        finally:
            current_tenant_id.reset(token)


def _on_cycle_error(exc: BaseException) -> None:
    """Discovery is best-effort: a failed cycle warns and the loop carries on."""
    logger.warning("portfolio_discovery_worker cycle failed open: %s", exc)


async def _tick() -> bool:
    """Run one discovery cycle, then idle until the next interval."""
    await _run_once()
    return False


_WORKER = BackgroundWorker(
    "portfolio_discovery_worker",
    _tick,
    interval=_IDLE_POLL_SECONDS,
    logger=logger,
    stop_timeout=_STOP_TIMEOUT,
    on_error=_on_cycle_error,
)


def run(interval: float | None = None) -> asyncio.Task:
    """Start the worker as a background task. Idempotent while active."""
    cfg = _cfg()
    interval_s = float(interval if interval is not None else cfg.get("interval_seconds") or _IDLE_POLL_SECONDS)
    return _WORKER.start(interval=interval_s)


async def stop() -> None:
    """Signal the worker to exit; await completion with force-cancel fallback."""
    await _WORKER.stop()


def _enabled() -> bool:
    return bool(_cfg().get("enabled", False))


def register(registry) -> None:
    """Register portfolio_discovery_worker on the WorkerRegistry (default disabled)."""
    from core_graph.worker.worker_registry import WorkerSpec

    registry.register(
        WorkerSpec(
            name="portfolio_discovery_worker",
            run=run,
            stop=stop,
            enabled_check=_enabled,
        )
    )
