"""
Shared system context for Whiskers Agent tools — package facade.

This package is the single source of truth for all shared singletons:

- ``mcp``           – the FastMCP application instance (registered by all plugins)
- ``vault``         – a single VaultService instance created once when ``DATABASE_URL`` is
                      available and reused by both ExternalOAuthRelay and PluginRegistry.
- ``oauth_provider``– the Layer-1 inbound RS256 JWT provider (requires DB).
- ``oauth_relay``   – the Layer-2 outbound per-plugin OAuth relay (requires DB).
- ``OAUTH_ENABLED`` – auto-detected from env; ``True`` when username/password are absent.
- ``_DB_AVAILABLE`` – ``True`` when ``DATABASE_URL`` env-var is non-empty.

Decomposed (Phase 1 modularity refactor) into:
- core/context/_env.py        — env-derived flags + run_if_db_available
- core/context/_app.py        — FastMCP app instance + vault/oauth_provider/oauth_relay
- core/context/_registries.py — route_registry/http_route_registry/tool_visibility/current_org_id

This module re-exports every symbol below as a module-level attribute so existing
``from core.context import X`` callers and ``patch("core.context.X")`` test mocks are unaffected.
Submodules are imported in dependency order (env -> app -> registries) to reproduce the exact
import-time side-effect sequence of the original single-file module.

Import pattern for other modules (unchanged)::

    from core.context import mcp, vault, oauth_relay, OAUTH_ENABLED, _DB_AVAILABLE
"""
from core.context._env import (
    OAUTH_ENABLED,
    MCP_SERVER_URL,
    FRONTEND_URL,
    _DB_AVAILABLE,
    run_if_db_available,
)
from core.context._app import (
    logger,
    oauth_provider,
    oauth_relay,
    vault,
    _oauth_svc,
    mcp_instructions,
    mcp_context_builder,
    mcp,
    mcp_middleware,
    subscription_bus,
    listen_handler,
)
from core.context._registries import (
    route_registry,
    http_route_registry,
    tool_visibility,
    current_org_id,
    current_tenant_id,
)
