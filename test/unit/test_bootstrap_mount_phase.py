"""MTU 4 contract: late-bound routes mount AFTER plugins load.

Covers the new bootstrap phase that drains the registry into the live router,
its ordering immediately after plugin loading, and that the broken empty-drain
in the entrypoint has been removed in favour of bind_app.
"""

import pathlib

import pytest

import core.context as ctx_mod
from core.bootstrap.phases import PHASES, phase_1_load_plugins, phase_mount_http_routes
from core.route_registry import HttpRouteRegistry


class _FakeMcp:
    def custom_route(self, *a, **k):
        def deco(fn):
            return fn
        return deco


class _FakeRouter:
    def __init__(self):
        self.routes = []


class _FakeApp:
    def __init__(self):
        self.router = _FakeRouter()


async def _ep(request):  # pragma: no cover
    return None


def test_phase_is_registered_right_after_plugin_load():
    runs = [p.run for p in PHASES]
    assert phase_mount_http_routes in runs
    assert phase_1_load_plugins in runs
    assert runs.index(phase_mount_http_routes) == runs.index(phase_1_load_plugins) + 1


@pytest.mark.asyncio
async def test_phase_drains_pending_into_live_router(monkeypatch):
    reg = HttpRouteRegistry(_FakeMcp())
    # Register BEFORE binding -> queued to pending (no app yet).
    reg.register_ws_route("/api/terminal/none/ws/{sid}", _ep, owner="term")
    app = _FakeApp()
    reg.bind_app(app)
    monkeypatch.setattr(ctx_mod, "http_route_registry", reg)

    await phase_mount_http_routes(object())

    assert any(getattr(r, "path", None) == "/api/terminal/none/ws/{sid}" for r in app.router.routes)


@pytest.mark.asyncio
async def test_phase_noop_under_stdio(monkeypatch):
    # Unbound registry (stdio) -> is_http_active False -> phase must not raise.
    reg = HttpRouteRegistry(_FakeMcp())
    monkeypatch.setattr(ctx_mod, "http_route_registry", reg)
    await phase_mount_http_routes(object())  # no exception == pass


def test_entrypoint_binds_app_and_drops_broken_drain():
    entrypoint = "whiskers_agent_mcp.py" if pathlib.Path("whiskers_agent_mcp.py").exists() else "whiskers_agent_mcp.py"
    src = pathlib.Path(entrypoint).read_text(encoding="utf-8")
    assert "bind_app(app)" in src
    # The premature empty-list drain is gone.
    assert "app.router.routes.extend(HTTP_ROUTES)" not in src
    assert "app.router.routes.extend(WS_ROUTES)" not in src
