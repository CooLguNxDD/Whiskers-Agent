"""MTU 2 contract: the HttpRouteRegistry singleton is constructed in core.context
and seeded with baseline policies, and core/ws_routes.py is a thin shim delegating
to it (backward-compat for un-migrated callers).
"""

from core.route_registry import HttpRouteRegistry, get_http_route_registry


def test_context_exposes_seeded_singleton():
    from core.context import http_route_registry

    assert isinstance(http_route_registry, HttpRouteRegistry)
    # Same object returned by the module-level accessor.
    assert get_http_route_registry() is http_route_registry
    # seed_default_policies() ran at construction.
    assert "/.well-known/" in http_route_registry.public_prefixes()
    assert "/plugins" in http_route_registry.gated_prefixes()
    assert "/" in http_route_registry.gated_exact()


def test_ws_routes_shim_surface_intact():
    import core.ws_routes as wr

    assert hasattr(wr, "register_ws_route")
    assert hasattr(wr, "register_http_route")
    # Legacy module-level lists retained (now always empty — nothing reads them).
    assert wr.WS_ROUTES == []
    assert wr.HTTP_ROUTES == []


def test_ws_routes_shim_delegates_to_registry():
    import core.ws_routes as wr
    from core.context import http_route_registry

    async def _ep(request):  # pragma: no cover - never invoked
        return None

    wr.register_http_route("/api/__shimtest__", _ep, methods=["GET"], name="shimtest")

    # Delegation: the route's policy is now visible via the registry accessors,
    # and it is tagged owner="legacy".
    assert "/api/__shimtest__" in http_route_registry.gated_prefixes()
    assert http_route_registry._decls["/api/__shimtest__"].owner == "legacy"
