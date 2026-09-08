"""MTU 5 contract: the cat_terminal_relay_plugin registers its WS legs + REST
control plane through the HttpRouteRegistry (not the legacy ws_routes lists),
tagged owner="cat_terminal_relay_plugin", with WS legs un-gated (self-auth) and
REST routes session-gated. on_unload must remove them (hot-reload teardown).
"""

import core.context as ctx_mod
from core.http_route_registry import AuthPolicy, HttpRouteRegistry

OWNER = "cat_terminal_relay_plugin"

REST_PATHS = {
    "/api/terminal/session_gated/hosts",
    "/api/terminal/session_gated/hosts/ws-ticket",
    "/api/terminal/session_gated/sessions",
    "/api/terminal/session_gated/sessions/{session_id}",
    "/api/terminal/session_gated/sessions/{session_id}/elevate",
    "/api/terminal/session_gated/totp/provision",
    "/api/terminal/session_gated/totp/status",
    "/api/terminal/session_gated/totp/verify",
    "/api/terminal/session_gated/password/provision",
    "/api/terminal/session_gated/host-token",
    "/api/terminal/session_gated/sandbox/elevate",
}
WS_PATHS = {
    "/api/terminal/none/ws/{session_id}",
    "/api/terminal/none/host/{ide_id}/session/{session_id}",
    "/api/terminal/none/host/{ide_id}",
    "/api/terminal/none/hosts/ws",
}


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


def _fresh_bound_registry():
    reg = HttpRouteRegistry(_FakeMcp())
    reg.bind_app(_FakeApp())
    return reg


def _mounted_paths(reg):
    return {getattr(r, "path", None) for r in reg._router.routes}


def test_register_routes_mounts_all_legs_with_owner(monkeypatch):
    reg = _fresh_bound_registry()
    monkeypatch.setattr(ctx_mod, "http_route_registry", reg)

    from plugins.cat_terminal_relay_plugin.routes import register_routes
    register_routes()

    mounted = _mounted_paths(reg)
    assert REST_PATHS <= mounted
    assert WS_PATHS <= mounted

    # All terminal decls owned by the plugin (for hot-reload grouping).
    for p in REST_PATHS | WS_PATHS:
        assert reg._decls[p].owner == OWNER


def test_rest_gated_ws_ungated(monkeypatch):
    reg = _fresh_bound_registry()
    monkeypatch.setattr(ctx_mod, "http_route_registry", reg)

    from plugins.cat_terminal_relay_plugin.routes import register_routes
    register_routes()

    # REST control plane is session-cookie gated.
    assert reg._decls["/api/terminal/session_gated/hosts"].auth_policy == AuthPolicy.SESSION_GATED
    # WS legs self-authenticate (JWT/ticket + CAT_TERMINAL_DEV_AUTH seam) -> NONE.
    assert reg._decls["/api/terminal/none/ws/{session_id}"].auth_policy == AuthPolicy.NONE


def test_on_unload_unmounts_owner(monkeypatch):
    reg = _fresh_bound_registry()
    monkeypatch.setattr(ctx_mod, "http_route_registry", reg)

    from plugins.cat_terminal_relay_plugin.routes import register_routes
    register_routes()
    assert REST_PATHS <= _mounted_paths(reg)

    removed = reg.unmount_owner(OWNER)
    assert removed == len(REST_PATHS) + len(WS_PATHS)
    assert not (REST_PATHS | WS_PATHS) & _mounted_paths(reg)
