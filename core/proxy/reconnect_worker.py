"""proxy_reconnect — retries proxies parked in ``status="error"`` so an outage heals on its own.

``ProxyManager._mark_unavailable`` unmounts a proxy whose tools listing or upstream
sessions exhausted their backoff, which is what stops a dead upstream from taking
the server down. Before this worker the only way back was a manual **Test** click.
Each errored proxy is retried through ``ProxyManager.test_proxy`` (the same rebuild a
click does) on its own exponential schedule: the first retry one interval after the
proxy is first seen errored, doubling up to ``graph.proxy_auto_reconnect_max_s``.

Config (``graph`` block): ``proxy_auto_reconnect`` (default true),
``proxy_auto_reconnect_interval_s`` (300), ``proxy_auto_reconnect_max_s`` (3600),
``proxy_auto_reconnect_max_attempts`` (0 = forever). Operator-disabled proxies are
``inactive``, never ``error``, so they are never touched.

Also publishes the ``proxy_sessions_open`` / ``proxy_sessions_waiting`` /
``proxy_breakers_open`` telemetry gauges while running.
"""

from __future__ import annotations

import asyncio
import logging
import time

from core_graph.worker.worker_loop import BackgroundWorker

logger = logging.getLogger("whiskers")

_TICK_SECONDS = 60.0
_STOP_TIMEOUT = 10.0
_DEFAULT_INTERVAL_S = 300.0
_DEFAULT_MAX_S = 3600.0

# name -> (failed_attempts, next_attempt_monotonic)
_schedule: dict[str, tuple[int, float]] = {}


def _cfg() -> dict:
    """Return the server ``graph`` config block (``{}`` when unavailable)."""
    from core.proxy.compose import _graph_config
    return _graph_config()


def _number(key: str, default: float, floor: float) -> float:
    """Read one numeric ``graph`` key via the proxy config reader (warn + default on junk)."""
    from core.proxy.compose import _config_number
    return _config_number(_cfg(), key, default, float, floor)


def _enabled() -> bool:
    """Whether auto-reconnect is on (``graph.proxy_auto_reconnect``, default true)."""
    return bool(_cfg().get("proxy_auto_reconnect", True))


def next_delay(attempts: int) -> float:
    """Seconds before the next retry after ``attempts`` failed ones: interval * 2**n, capped."""
    interval = _number("proxy_auto_reconnect_interval_s", _DEFAULT_INTERVAL_S, 1.0)
    cap = max(_number("proxy_auto_reconnect_max_s", _DEFAULT_MAX_S, 1.0), interval)
    return min(interval * (2 ** min(attempts, 30)), cap)


async def _tick() -> bool:
    """Retry every errored proxy whose slot is due. Never raises."""
    if not _enabled():
        return False
    from core.proxy.proxy_manager import proxy_manager

    try:
        names = await proxy_manager.errored_proxy_names()
    except Exception:
        logger.exception("proxy_reconnect: listing errored proxies failed")
        return False

    for gone in set(_schedule) - set(names):
        _schedule.pop(gone, None)  # recovered, disabled or removed since last tick

    max_attempts = int(_number("proxy_auto_reconnect_max_attempts", 0, 0))
    now = time.monotonic()
    for name in names:
        if name not in _schedule:
            # First sighting: give the upstream one interval before hammering it again.
            _schedule[name] = (0, now + next_delay(0))
            continue
        attempts, due = _schedule[name]
        if now < due or (max_attempts and attempts >= max_attempts):
            continue
        try:
            row = await proxy_manager.test_proxy(name)
            ok = row.get("status") == "active"
        except Exception:
            logger.exception("proxy_reconnect: reconnect of %s raised", name)
            ok = False
        if ok:
            _schedule.pop(name, None)
            logger.info("proxy_reconnect: %s reconnected after %d failed attempt(s)", name, attempts)
            _emit_reconnected(name, attempts + 1)
        else:
            attempts += 1
            delay = next_delay(attempts)
            _schedule[name] = (attempts, time.monotonic() + delay)
            if max_attempts and attempts >= max_attempts:
                logger.error(
                    "proxy_reconnect: %s still down after %d attempts; waiting for a manual Reconnect",
                    name, attempts,
                )
            else:
                logger.warning(
                    "proxy_reconnect: %s still down (attempt %d); next try in %.0fs", name, attempts, delay
                )
    return False


def _emit_reconnected(name: str, attempts: int) -> None:
    """Emit ``proxy.reconnected`` on the plugin EventBus (best effort)."""
    try:
        from core.plugin_loader.plugin_registry import get_registry
        from core.proxy.models import ProxyReconnectedEvent
        get_registry().events.emit(
            "proxy.reconnected", ProxyReconnectedEvent(name=name, attempts=attempts)
        )
    except Exception as ev_err:
        logger.debug("EventBus emit skipped: %s", ev_err)


def _gauges() -> dict:
    """Gauge key -> provider over ``compose.session_stats``."""
    from core.proxy.compose import session_stats
    return {
        "proxy_sessions_open": lambda: session_stats()["open"],
        "proxy_sessions_waiting": lambda: session_stats()["waiting"],
        "proxy_breakers_open": lambda: session_stats()["breakers_open"],
    }


def _set_gauges(on: bool) -> None:
    """Register (or drop) the proxy session gauges; telemetry being unavailable is not fatal."""
    try:
        from core.telemetry.collector import collector
        for key, provider in _gauges().items():
            if on:
                collector.register_gauge_provider(key, provider)
            else:
                collector.unregister_gauge_provider(key)
    except Exception:
        logger.debug("proxy_reconnect: telemetry gauges unavailable", exc_info=True)


_WORKER = BackgroundWorker(
    "proxy_reconnect",
    _tick,
    interval=_TICK_SECONDS,
    logger=logger,
    stop_timeout=_STOP_TIMEOUT,
)


def run() -> asyncio.Task:
    """Start the worker (and its gauges). Idempotent while active."""
    _set_gauges(True)
    return _WORKER.start()


async def stop() -> None:
    """Stop the worker and drop its gauges."""
    await _WORKER.stop()
    _set_gauges(False)
    _schedule.clear()


def register(registry) -> None:
    """Register proxy_reconnect on the WorkerRegistry (gated by ``graph.proxy_auto_reconnect``)."""
    from core_graph.worker.worker_registry import WorkerSpec
    registry.register(
        WorkerSpec(name="proxy_reconnect", run=run, stop=stop, enabled_check=_enabled)
    )
