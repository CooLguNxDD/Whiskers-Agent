"""




System health REST API for the admin shell Live status strip.

Endpoints
---------
GET    /api/health/public             — Liveness probe — returns ok without session or dependency checks.
GET    /api/health/session_gated      — Aggregated system health for the admin shell Live status strip.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from db_layer.telemetry_store import telemetry_store

logger = logging.getLogger("whiskers_agent")

# Process start time — set once at module import (server process lifetime).
_PROCESS_START_TS = time.time()


def resolve_instance_name() -> str:
    """Resolve the display instance name for the admin shell node pill."""
    env_name = (
        os.environ.get("WHISKERS_INSTANCE_NAME")
        or os.environ.get("WHISKERS_INSTANCE_NAME")
        or ""
    ).strip()
    if env_name:
        return env_name
    try:
        host = socket.gethostname().strip()
        if host:
            return host
    except Exception:
        logger.debug("health_routes.py: swallowed exception", exc_info=True)
    try:
        from utils.server_config import SERVER_CONFIG
        name = (SERVER_CONFIG.get("context") or {}).get("mcp_server_name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except Exception:
        logger.debug("health_routes.py: swallowed exception", exc_info=True)
    return "whiskers_agent"


async def _count_plugins() -> tuple[dict[str, int], bool, int, str]:
    """Count plugins and resolve system tier. Soft-fails."""
    system_tier = 1
    system_tier_name = "LITE"
    try:
        from core.plugin_loader.plugin_registry import get_registry, Tier

        registry = get_registry()
        system_tier = int(getattr(registry, "system_tier", 1) or 1)
        try:
            system_tier_name = Tier(system_tier).name
        except (ValueError, TypeError):
            system_tier_name = str(system_tier)

        loaded = list(getattr(registry.lifecycle, "_plugins", []) or [])
        loaded_map = {getattr(p, "name", ""): p for p in loaded if getattr(p, "name", "")}

        try:
            from db_layer.plugin_registry_store import DBPluginRegistry

            records = await DBPluginRegistry().get_all()
            total = len(records)
            enabled = sum(1 for r in records if getattr(r, "is_active", False))
            # Include in-memory-only plugins not yet in DB
            for name in loaded_map:
                if not any(r.id == name for r in records):
                    total += 1
                    enabled += 1
            return {"total": total, "enabled": enabled}, True, system_tier, system_tier_name
        except Exception as db_exc:
            logger.debug("health: plugin DB unavailable, using in-memory: %s", db_exc)
            total = len(loaded_map)
            return {"total": total, "enabled": total}, True, system_tier, system_tier_name
    except RuntimeError:
        return {"total": 0, "enabled": 0}, False, system_tier, system_tier_name
    except Exception as exc:
        logger.debug("health: plugin count failed: %s", exc)
        return {"total": 0, "enabled": 0}, False, system_tier, system_tier_name


async def _count_tunnels() -> tuple[dict[str, int], bool]:
    """Count upstream proxies (tunnels). Soft-fails to zeros."""
    try:
        from core.proxy.proxy_manager import proxy_manager

        proxies = await proxy_manager.list_proxies()
        total = len(proxies)
        active = sum(1 for p in proxies if (p.get("status") or "") == "active")
        return {"total": total, "active": active}, True
    except Exception as exc:
        logger.debug("health: tunnel count failed: %s", exc)
        return {"total": 0, "active": 0}, False


def _metrics_from_telemetry() -> tuple[dict[str, float | int], bool]:
    """Pull live p50/p99/success_rate from the in-memory collector."""
    try:
        from core.telemetry.collector import collector

        snap = collector.snapshot()
        summary = snap.get("summary") or {}
        total_calls = int(summary.get("total_calls") or 0)
        if total_calls <= 0:
            return {
                "success_rate": 0.0,
                "p50_latency_ms": 0,
                "p99_latency_ms": 0,
            }, True  # telemetry ok, but empty — caller may fall back
        return {
            "success_rate": float(summary.get("success_rate") or 0.0),
            "p50_latency_ms": int(summary.get("p50_latency") or 0),
            "p99_latency_ms": int(summary.get("p99_latency") or 0),
        }, True
    except Exception as exc:
        logger.debug("health: telemetry snapshot failed: %s", exc)
        return {
            "success_rate": 0.0,
            "p50_latency_ms": 0,
            "p99_latency_ms": 0,
        }, False


async def _metrics_from_db_24h() -> tuple[dict[str, float | int], bool]:
    """Fall back to last-24h KPI from tool_call_events when live samples are empty.

    Filtered by the active tenant (strict multi-tenant isolation). Live
    in-memory collector metrics remain process-global for this release.

    When no tenant is on the contextvar, default to tenant 1 — intentional
    admin-metrics fallback so the health strip still renders for session
    calls that lack ocat_tenant. This is an aggregate KPI, not per-record PII.
    """
    from core.context import current_tenant_id

    tenant_id = current_tenant_id.get()
    if tenant_id is None:
        # Intentional admin aggregate fallback (see docstring) — do not hard-reject.
        logger.warning(
            "health: no tenant_id on context; defaulting DB KPI filter to tenant_id=1"
        )
        tenant_id = 1
    try:
        return await telemetry_store.kpi_last_24h(tenant_id), True
    except Exception as exc:
        logger.debug("health: DB KPI fallback failed: %s", exc)
        return {
            "success_rate": 0.0,
            "p50_latency_ms": 0,
            "p99_latency_ms": 0,
        }, False


async def _check_db() -> bool:
    """Lightweight DB connectivity probe via telemetry_store."""
    return await telemetry_store.ping()


def _derive_status(registry_ok: bool, checks: dict[str, bool]) -> str:
    """healthy | degraded | unhealthy from soft-check results."""
    if not registry_ok:
        return "unhealthy"
    if not checks.get("db", True) or not checks.get("telemetry", True) or not checks.get("proxies", True):
        return "degraded"
    return "healthy"


@http_route_registry.route(
    route="health",
    endpoint="",
    methods=["GET"],
    name="api_health_ok",
    auth_policy=AuthPolicy.PUBLIC,
    owner="api.health",
)
async def get_health_ok(request: Request) -> Response:
    """Liveness probe — returns ok without session or dependency checks."""
    return JSONResponse({"ok": True})


@http_route_registry.route(
    route="health",
    endpoint="",
    methods=["GET"],
    name="api_system_health",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.health",
)
async def get_system_health(request: Request) -> Response:
    """Aggregated system health for the admin shell Live status strip."""
    plugins, registry_ok, system_tier, system_tier_name = await _count_plugins()
    tunnels, proxies_ok = await _count_tunnels()

    metrics, telemetry_ok = _metrics_from_telemetry()
    live_empty = (
        int(metrics.get("p50_latency_ms") or 0) == 0
        and float(metrics.get("success_rate") or 0) == 0.0
    )
    # Fall back to 24h DB KPI when live collector is empty or unavailable
    if (not telemetry_ok) or live_empty:
        db_metrics, db_metrics_ok = await _metrics_from_db_24h()
        if db_metrics_ok and (
            float(db_metrics.get("success_rate") or 0) > 0
            or int(db_metrics.get("p50_latency_ms") or 0) > 0
        ):
            metrics = db_metrics
        if not telemetry_ok:
            # Prefer DB path for the telemetry check when live collector is broken
            telemetry_ok = db_metrics_ok

    db_ok = await _check_db()

    checks = {
        "registry": registry_ok,
        "db": db_ok,
        "telemetry": telemetry_ok,
        "proxies": proxies_ok,
    }
    status = _derive_status(registry_ok, checks)

    payload: dict[str, Any] = {
        "status": status,
        "instance_name": resolve_instance_name(),
        "uptime_seconds": int(max(0, time.time() - _PROCESS_START_TS)),
        "plugins": plugins,
        "tunnels": tunnels,
        "metrics": metrics,
        "system_tier": system_tier,
        "system_tier_name": system_tier_name,
        "checks": checks,
    }
    return JSONResponse(payload, status_code=200)
