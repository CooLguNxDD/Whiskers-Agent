"""MTU 6 contract: the api/* and oauth/* route modules declare their routes
through `http_route_registry.route(...)` (which delegates to mcp.custom_route at
import time) with a per-route auth_policy. We assert representative routes whose
EXACT paths are NOT in the seeded baseline, so a passing test proves migration
happened (not merely that the seed covers the prefix).
"""

import importlib

from core.context import http_route_registry


def _import_route_modules():
    for mod in (
        "api.admin_routes",
        "api.plugin_routes",
        "api.config_routes",
        "api.proxy_routes",
        "api.playground_routes",
        "api.log_routes",
        "api.tool_routes",
        "api.route_routes",
        "oauth.oauth_routes",
    ):
        importlib.import_module(mod)


def test_public_routes_declared_through_registry():
    _import_route_modules()
    pub = http_route_registry.public_prefixes()
    # Exact paths absent from seed_default_policies() -> only present if migrated.
    assert "/.well-known/jwks.json" in pub
    assert "/oauth/connect/{auth_state}" in pub
    # Declared by a real module, not the synthetic policy seed.
    assert http_route_registry._decls["/.well-known/jwks.json"].owner != "__policy__"


def test_gated_api_routes_declared_through_registry():
    _import_route_modules()
    gated = http_route_registry.gated_prefixes()
    assert "/api/plugins/session_gated" in gated
    assert "/api/config/session_gated/llm" in gated
    assert http_route_registry._decls["/api/plugins/session_gated"].owner != "__policy__"


def test_admin_public_routes_declared():
    _import_route_modules()
    decls = http_route_registry._decls
    # /api/admin/public/login is migrated as PUBLIC and owned by a real module.
    assert "/api/admin/public/login" in decls
    assert decls["/api/admin/public/login"].auth_policy.value == "public"
    assert decls["/api/admin/public/login"].owner != "__policy__"
