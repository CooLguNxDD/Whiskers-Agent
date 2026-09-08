"""Unit tests for the system health aggregation endpoint helpers and route."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from api import health_routes


def test_resolve_instance_name_prefers_env(monkeypatch):
    monkeypatch.setenv("WHISKERS_INSTANCE_NAME", "node-a.prod")
    assert health_routes.resolve_instance_name() == "node-a.prod"


def test_resolve_instance_name_falls_back_to_hostname(monkeypatch):
    monkeypatch.delenv("WHISKERS_INSTANCE_NAME", raising=False)
    monkeypatch.setattr(health_routes.socket, "gethostname", lambda: "host-xyz")
    assert health_routes.resolve_instance_name() == "host-xyz"


def test_derive_status_levels():
    assert health_routes._derive_status(False, {"db": True, "telemetry": True, "proxies": True}) == "unhealthy"
    assert health_routes._derive_status(True, {"db": False, "telemetry": True, "proxies": True}) == "degraded"
    assert health_routes._derive_status(True, {"db": True, "telemetry": True, "proxies": True}) == "healthy"


@pytest.mark.asyncio
async def test_get_health_ok():
    """Public liveness route returns ok with no session or deps."""
    scope = {"type": "http", "method": "GET", "path": "/api/health/public", "headers": []}
    request = Request(scope)
    res = await health_routes.get_health_ok(request)
    assert res.status_code == 200
    import json

    assert json.loads(res.body) == {"ok": True}


@pytest.mark.asyncio
async def test_count_plugins_from_db(monkeypatch):
    mock_plugin = MagicMock()
    mock_plugin.name = "fake_plugin"

    mock_registry = MagicMock()
    mock_registry.lifecycle._plugins = [mock_plugin]
    mock_registry.system_tier = 1

    rec_active = MagicMock()
    rec_active.id = "fake_plugin"
    rec_active.is_active = True
    rec_inactive = MagicMock()
    rec_inactive.id = "other_plugin"
    rec_inactive.is_active = False

    mock_store = MagicMock()
    mock_store.get_all = AsyncMock(return_value=[rec_active, rec_inactive])

    monkeypatch.setattr(
        "core.plugin_loader.plugin_registry.get_registry",
        lambda: mock_registry,
    )
    with patch("db_layer.plugin_registry_store.DBPluginRegistry", return_value=mock_store):
        counts, ok, tier, tier_name = await health_routes._count_plugins()

    assert ok is True
    assert counts == {"total": 2, "enabled": 1}
    assert tier == 1
    assert tier_name == "LITE"


@pytest.mark.asyncio
async def test_count_plugins_registry_missing(monkeypatch):
    def _boom():
        raise RuntimeError("registry not ready")

    monkeypatch.setattr(
        "core.plugin_loader.plugin_registry.get_registry",
        _boom,
    )
    counts, ok, _tier, _name = await health_routes._count_plugins()
    assert ok is False
    assert counts == {"total": 0, "enabled": 0}


@pytest.mark.asyncio
async def test_count_tunnels():
    with patch(
        "core.proxy.proxy_manager.proxy_manager.list_proxies",
        new=AsyncMock(
            return_value=[
                {"name": "a", "status": "active"},
                {"name": "b", "status": "inactive"},
                {"name": "c", "status": "active"},
            ]
        ),
    ):
        counts, ok = await health_routes._count_tunnels()
    assert ok is True
    assert counts == {"total": 3, "active": 2}


@pytest.mark.asyncio
async def test_count_tunnels_soft_fail():
    with patch(
        "core.proxy.proxy_manager.proxy_manager.list_proxies",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        counts, ok = await health_routes._count_tunnels()
    assert ok is False
    assert counts == {"total": 0, "active": 0}


def test_metrics_from_telemetry_with_samples():
    fake = MagicMock()
    fake.snapshot.return_value = {
        "summary": {
            "total_calls": 10,
            "success_rate": 99.5,
            "p50_latency": 47,
            "p99_latency": 120,
        }
    }
    with patch("core.telemetry.collector.collector", fake):
        metrics, ok = health_routes._metrics_from_telemetry()
    assert ok is True
    assert metrics["success_rate"] == 99.5
    assert metrics["p50_latency_ms"] == 47
    assert metrics["p99_latency_ms"] == 120


@pytest.mark.asyncio
async def test_metrics_from_db_24h_filters_by_tenant(monkeypatch):
    """DB KPI fallback must bind tenant_id (never unscoped)."""
    from core.context import current_tenant_id

    captured: dict = {}

    async def fake_kpi(tenant_id: int):
        captured["tenant_id"] = tenant_id
        return {"success_rate": 100.0, "p50_latency_ms": 10, "p99_latency_ms": 20}

    monkeypatch.setattr(health_routes.telemetry_store, "kpi_last_24h", fake_kpi)
    token = current_tenant_id.set(42)
    try:
        metrics, ok = await health_routes._metrics_from_db_24h()
    finally:
        current_tenant_id.reset(token)

    assert ok is True
    assert metrics["success_rate"] == 100.0
    assert captured["tenant_id"] == 42


@pytest.mark.asyncio
async def test_get_system_health_route_shape(monkeypatch):
    monkeypatch.setenv("WHISKERS_INSTANCE_NAME", "test-node")
    monkeypatch.setattr(
        health_routes,
        "_count_plugins",
        AsyncMock(return_value=({"total": 4, "enabled": 3}, True, 1, "LITE")),
    )
    monkeypatch.setattr(
        health_routes,
        "_count_tunnels",
        AsyncMock(return_value=({"total": 2, "active": 1}, True)),
    )
    monkeypatch.setattr(
        health_routes,
        "_metrics_from_telemetry",
        lambda: (
            {"success_rate": 100.0, "p50_latency_ms": 12, "p99_latency_ms": 40},
            True,
        ),
    )
    monkeypatch.setattr(health_routes, "_check_db", AsyncMock(return_value=True))

    scope = {"type": "http", "method": "GET", "path": "/api/health", "headers": []}
    request = Request(scope)
    res = await health_routes.get_system_health(request)
    assert res.status_code == 200
    body = res.body
    import json

    data = json.loads(body)
    assert data["status"] == "healthy"
    assert data["instance_name"] == "test-node"
    assert data["plugins"] == {"total": 4, "enabled": 3}
    assert data["tunnels"] == {"total": 2, "active": 1}
    assert data["metrics"]["p50_latency_ms"] == 12
    assert data["system_tier_name"] == "LITE"
    assert data["checks"]["registry"] is True
    assert "uptime_seconds" in data


@pytest.mark.asyncio
async def test_get_system_health_degraded_when_db_down(monkeypatch):
    monkeypatch.setenv("WHISKERS_INSTANCE_NAME", "test-node")
    monkeypatch.setattr(
        health_routes,
        "_count_plugins",
        AsyncMock(return_value=({"total": 1, "enabled": 1}, True, 1, "LITE")),
    )
    monkeypatch.setattr(
        health_routes,
        "_count_tunnels",
        AsyncMock(return_value=({"total": 0, "active": 0}, True)),
    )
    monkeypatch.setattr(
        health_routes,
        "_metrics_from_telemetry",
        lambda: (
            {"success_rate": 0.0, "p50_latency_ms": 0, "p99_latency_ms": 0},
            True,
        ),
    )
    monkeypatch.setattr(
        health_routes,
        "_metrics_from_db_24h",
        AsyncMock(
            return_value=(
                {"success_rate": 0.0, "p50_latency_ms": 0, "p99_latency_ms": 0},
                False,
            )
        ),
    )
    monkeypatch.setattr(health_routes, "_check_db", AsyncMock(return_value=False))

    scope = {"type": "http", "method": "GET", "path": "/api/health", "headers": []}
    request = Request(scope)
    res = await health_routes.get_system_health(request)
    import json

    data = json.loads(res.body)
    assert data["status"] == "degraded"
    assert data["checks"]["db"] is False
