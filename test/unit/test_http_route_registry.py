"""Unit tests for the unified HttpRouteRegistry (core/http_route_registry.py).

These lock the contract for MTU 1: declaration data model, dual-backend
dispatch (early-bound mcp.custom_route vs late-bound router append), hot-reload
mount/unmount by path and owner, the policy accessors consumed by middleware,
and the module singleton. No server lifecycle is involved here.
"""

import pytest
from starlette.routing import Route, WebSocketRoute

from core.route_registry import (
    AuthPolicy,
    HttpRouteRegistry,
    RouteDeclaration,
    get_http_route_registry,
    _set_http_route_registry,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _FakeMcp:
    """Records custom_route registrations (the early-bound backend)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def custom_route(self, path, methods=None, name=None, include_in_schema=True):
        self.calls.append((path, tuple(methods or ()), name))

        def deco(fn):
            return fn

        return deco


class _FakeRouter:
    def __init__(self) -> None:
        self.routes: list = []


class _FakeApp:
    def __init__(self) -> None:
        self.router = _FakeRouter()


async def _endpoint(request):  # pragma: no cover - never called in unit tests
    return None


def _paths(router) -> set:
    return {getattr(r, "path", None) for r in router.routes}


# ---------------------------------------------------------------------------
# RouteDeclaration
# ---------------------------------------------------------------------------

def test_declaration_defaults():
    d = RouteDeclaration(path="/x", endpoint=_endpoint)
    assert d.kind == "http"
    assert d.methods == ("GET",)
    assert d.auth_policy == AuthPolicy.NONE
    assert d.owner == "core"
    assert d.match_mode == "prefix"


def test_declaration_to_starlette_http_and_ws():
    http = RouteDeclaration(path="/h", endpoint=_endpoint, methods=("POST",), name="h")
    obj = http.to_starlette()
    assert isinstance(obj, Route)
    assert obj.path == "/h"
    assert "POST" in obj.methods

    ws = RouteDeclaration(path="/w", endpoint=_endpoint, kind="ws", name="w")
    wobj = ws.to_starlette()
    assert isinstance(wobj, WebSocketRoute)
    assert wobj.path == "/w"


# ---------------------------------------------------------------------------
# Unbound (stdio) behaviour — no app means mounts are no-ops
# ---------------------------------------------------------------------------

def test_unbound_is_inactive_and_mounts_noop():
    reg = HttpRouteRegistry(_FakeMcp())
    assert reg.is_http_active is False
    d = RouteDeclaration(path="/late", endpoint=_endpoint)
    assert reg.mount(d) is False
    assert reg.drain_pending() == 0


def test_early_bound_http_goes_through_custom_route():
    mcp = _FakeMcp()
    reg = HttpRouteRegistry(mcp)
    reg.register_http_route("/api/foo", _endpoint, methods=["GET"], owner="api.test")
    # App not bound -> early-bound backend records the custom_route call.
    assert ("/api/foo", ("GET",), None) in [(c[0], c[1], c[2]) for c in mcp.calls]


def test_early_bound_ws_queues_pending_then_drains_after_bind():
    reg = HttpRouteRegistry(_FakeMcp())
    reg.register_ws_route("/api/terminal/none/ws/{sid}", _endpoint, owner="term")
    # WS has no early-bound backend -> queued.
    app = _FakeApp()
    reg.bind_app(app)
    assert reg.is_http_active is True
    assert reg.drain_pending() == 1
    assert "/api/terminal/none/ws/{sid}" in _paths(app.router)


# ---------------------------------------------------------------------------
# Late-bound mount / unmount (hot-reload)
# ---------------------------------------------------------------------------

def test_late_bound_declare_mounts_immediately():
    reg = HttpRouteRegistry(_FakeMcp())
    app = _FakeApp()
    reg.bind_app(app)
    reg.register_http_route("/api/bar", _endpoint, methods=["GET"], owner="api.test")
    assert "/api/bar" in _paths(app.router)


def test_unmount_removes_live_route():
    reg = HttpRouteRegistry(_FakeMcp())
    app = _FakeApp()
    reg.bind_app(app)
    reg.register_http_route("/api/bar", _endpoint, methods=["GET"], owner="api.test")
    assert reg.unmount("/api/bar") is True
    assert "/api/bar" not in _paths(app.router)


def test_mount_is_idempotent_on_path():
    reg = HttpRouteRegistry(_FakeMcp())
    app = _FakeApp()
    reg.bind_app(app)
    reg.register_http_route("/api/dup", _endpoint, methods=["GET"], owner="api.test")
    reg.register_http_route("/api/dup", _endpoint, methods=["GET"], owner="api.test")
    assert sum(1 for r in app.router.routes if getattr(r, "path", None) == "/api/dup") == 1


def test_owner_mount_and_unmount():
    reg = HttpRouteRegistry(_FakeMcp())
    app = _FakeApp()
    reg.bind_app(app)
    reg.register_http_route("/api/a", _endpoint, methods=["GET"], owner="plug")
    reg.register_http_route("/api/b", _endpoint, methods=["GET"], owner="plug")
    reg.register_http_route("/api/c", _endpoint, methods=["GET"], owner="other")

    removed = reg.unmount_owner("plug")
    assert removed == 2
    assert _paths(app.router) == {"/api/c"}

    added = reg.mount_owner("plug")
    assert added == 2
    assert _paths(app.router) == {"/api/a", "/api/b", "/api/c"}


# ---------------------------------------------------------------------------
# Policy accessors
# ---------------------------------------------------------------------------

def test_policy_accessors_reflect_declarations():
    reg = HttpRouteRegistry(_FakeMcp())
    app = _FakeApp()
    reg.bind_app(app)
    reg.declare(RouteDeclaration(path="/pub/", endpoint=_endpoint,
                                 auth_policy=AuthPolicy.PUBLIC, owner="o"))
    reg.declare(RouteDeclaration(path="/api/", endpoint=_endpoint,
                                 auth_policy=AuthPolicy.SESSION_GATED, owner="o"))
    reg.declare(RouteDeclaration(path="/", endpoint=_endpoint,
                                 auth_policy=AuthPolicy.SESSION_GATED,
                                 match_mode="exact", owner="o"))
    assert "/pub/" in reg.public_prefixes()
    assert "/api/" in reg.gated_prefixes()
    assert "/" in reg.gated_exact()
    assert "/" not in reg.gated_prefixes()


def test_seed_default_policies_baseline():
    reg = HttpRouteRegistry(_FakeMcp())
    reg.seed_default_policies()
    pub = reg.public_prefixes()
    gated = reg.gated_prefixes()
    assert "/.well-known/" in pub
    assert "/oauth/plugin/" in pub
    assert "/api/" not in gated
    assert "/oauth/authorize" in gated
    # Synthetic policy decls must never be mounted.
    app = _FakeApp()
    reg.bind_app(app)
    reg.drain_pending()
    assert app.router.routes == []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

def test_singleton_set_and_get():
    import core.route_registry as _mod

    original = _mod.http_route_registry.get_http_route_registry() if _mod.http_route_registry._http_route_registry is not None else None
    try:
        reg = HttpRouteRegistry(_FakeMcp())
        _set_http_route_registry(reg)
        assert get_http_route_registry() is reg
    finally:
        # Restore the live singleton so test ordering can't pollute other modules.
        _set_http_route_registry(original)

def test_clear():
    reg = HttpRouteRegistry(_FakeMcp())
    reg.declare(RouteDeclaration(path="/foo", endpoint=lambda x: x))
    assert "/foo" in reg._decls
    reg.clear()
    assert len(reg._decls) == 0
    assert len(reg._mounted) == 0
    assert len(reg._pending_late) == 0


def test_structured_route_registration():
    reg = HttpRouteRegistry(_FakeMcp())
    app = _FakeApp()
    reg.bind_app(app)

    # Register via route keyword, with endpoint subpath as string and handler callable
    reg.register_http_route(
        route="foo",
        endpoint="bar",
        auth_policy=AuthPolicy.SESSION_GATED,
        handler=_endpoint,
        methods=["GET"],
        owner="api.test",
    )
    assert "/api/foo/session_gated/bar" in _paths(app.router)

    # Register via route decorator
    @reg.route(route="hello", auth_policy=AuthPolicy.PUBLIC, endpoint="world")
    def hello_world_endpoint(request):
        return None

    assert "/api/hello/public/world" in _paths(app.router)


# ---------------------------------------------------------------------------
# required_scopes_for — exact + param-aware template matching
# ---------------------------------------------------------------------------


def test_required_scopes_for_exact_match():
    reg = HttpRouteRegistry(_FakeMcp())
    reg.declare(
        RouteDeclaration(
            path="/api/config/session_gated/plugin-gates",
            endpoint=_endpoint,
            auth_policy=AuthPolicy.SESSION_GATED,
            required_scopes=("core:config:write",),
            owner="api.config",
        )
    )
    assert reg.required_scopes_for("/api/config/session_gated/plugin-gates") == ("core:config:write",)


def test_required_scopes_for_no_match_returns_empty():
    reg = HttpRouteRegistry(_FakeMcp())
    assert reg.required_scopes_for("/api/nonexistent") == ()


def test_required_scopes_for_param_template_matches_concrete_path():
    reg = HttpRouteRegistry(_FakeMcp())
    reg.declare(
        RouteDeclaration(
            path="/api/config/session_gated/plugin-gates/{plugin_id}",
            endpoint=_endpoint,
            auth_policy=AuthPolicy.SESSION_GATED,
            required_scopes=("core:config:write",),
            owner="api.config",
        )
    )
    assert (
        reg.required_scopes_for("/api/config/session_gated/plugin-gates/jules_plugin")
        == ("core:config:write",)
    )
    # A different segment count must not match.
    assert reg.required_scopes_for("/api/config/session_gated/plugin-gates/jules_plugin/extra") == ()


def test_required_scopes_for_param_template_does_not_cross_segment_boundaries():
    reg = HttpRouteRegistry(_FakeMcp())
    reg.declare(
        RouteDeclaration(
            path="/api/config/session_gated/model-roles/{role_id}",
            endpoint=_endpoint,
            auth_policy=AuthPolicy.SESSION_GATED,
            required_scopes=("core:config:write",),
            owner="api.config",
        )
    )
    # {role_id} must match one segment only, never swallow a trailing slash.
    assert reg.required_scopes_for("/api/config/session_gated/model-roles/") == ()
    assert (
        reg.required_scopes_for("/api/config/session_gated/model-roles/abc-123")
        == ("core:config:write",)
    )

