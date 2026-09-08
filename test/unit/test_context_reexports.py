"""Regression guard: lock the public re-export surface of core.context before Phase 1 (the
context.py -> core/context/ package decomposition) so a dropped/renamed symbol fails loudly."""
import core.context as ctx

EXPECTED_ATTRS = [
    "OAUTH_ENABLED", "MCP_SERVER_URL", "FRONTEND_URL", "_DB_AVAILABLE", "run_if_db_available",
    "oauth_provider", "oauth_relay", "vault", "mcp_instructions", "mcp_context_builder", "mcp",
    "route_registry", "http_route_registry", "tool_visibility", "current_org_id",
]


def test_context_module_exposes_all_expected_attrs():
    missing = [name for name in EXPECTED_ATTRS if not hasattr(ctx, name)]
    assert not missing, f"core.context is missing expected attributes: {missing}"


def test_context_attrs_individually_importable():
    from core.context import (
        OAUTH_ENABLED, MCP_SERVER_URL, FRONTEND_URL, _DB_AVAILABLE, run_if_db_available,
        oauth_provider, oauth_relay, vault, mcp_instructions, mcp_context_builder, mcp,
        route_registry, http_route_registry, tool_visibility, current_org_id,
    )
