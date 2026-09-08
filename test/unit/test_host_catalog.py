"""Unit tests for host HttpRouteRegistry → OperationCatalog mirroring."""

from __future__ import annotations

from core.route_registry.host_catalog import (
    clear_host_catalog_index,
    declaration_to_operations,
    host_ops_snapshot,
    upsert_host_declaration,
)
from core.route_registry.http_route_registry import AuthPolicy, RouteDeclaration
from core.route_registry.operation_catalog import (
    _reset_operation_catalog_for_tests,
    get_operation_catalog,
)


def setup_function() -> None:
    _reset_operation_catalog_for_tests()
    clear_host_catalog_index()


def teardown_function() -> None:
    clear_host_catalog_index()
    _reset_operation_catalog_for_tests()


async def _handler(request):  # pragma: no cover - never invoked
    """Stub endpoint."""
    return None


def test_declaration_to_operations_path_params() -> None:
    decl = RouteDeclaration(
        path="/api/plugins/session_gated/{plugin_id}/enable",
        endpoint=_handler,
        methods=("POST",),
        name="api_enable_plugin",
        auth_policy=AuthPolicy.SESSION_GATED,
        owner="api.plugins",
    )
    ops = declaration_to_operations(decl)
    assert len(ops) == 1
    op = ops[0]
    assert op.plugin_id == "api.plugins"
    assert op.operation_id == "api_enable_plugin"
    assert op.http is not None
    assert op.http.method == "POST"
    assert op.http.path_template == "/api/plugins/session_gated/{plugin_id}/enable"
    assert "plugin_id" in (op.input_schema.get("properties") or {})
    assert "plugin_id" in (op.input_schema.get("required") or [])


def test_skip_policy_and_ws() -> None:
    pol = RouteDeclaration(
        path="/oauth/authorize",
        endpoint=None,
        auth_policy=AuthPolicy.SESSION_GATED,
        owner="__policy__",
    )
    assert declaration_to_operations(pol) == []

    ws = RouteDeclaration(
        path="/api/analytics/session_gated/ws",
        endpoint=_handler,
        kind="ws",
        name="analytics_ws",
        owner="api.analytics",
    )
    assert declaration_to_operations(ws) == []


def test_upsert_publishes_both_methods_same_path() -> None:
    """POST and DELETE on the same path are separate catalog ops (by name)."""
    post = RouteDeclaration(
        path="/api/plugins/session_gated/{plugin_id}/enable",
        endpoint=_handler,
        methods=("POST",),
        name="api_enable_plugin",
        auth_policy=AuthPolicy.SESSION_GATED,
        owner="api.plugins",
    )
    delete = RouteDeclaration(
        path="/api/plugins/session_gated/{plugin_id}/enable",
        endpoint=_handler,
        methods=("DELETE",),
        name="api_disable_plugin",
        auth_policy=AuthPolicy.SESSION_GATED,
        owner="api.plugins",
    )
    upsert_host_declaration(post)
    upsert_host_declaration(delete)

    cat = get_operation_catalog()
    assert cat.get("api.plugins", "api_enable_plugin") is not None
    assert cat.get("api.plugins", "api_disable_plugin") is not None
    assert len(host_ops_snapshot()) == 2


def test_public_visibility() -> None:
    decl = RouteDeclaration(
        path="/api/admin/public/exists",
        endpoint=_handler,
        methods=("GET",),
        name="admin_exists",
        auth_policy=AuthPolicy.PUBLIC,
        owner="api.admin",
    )
    ops = declaration_to_operations(decl)
    assert ops[0].visibility.value == "public_catalog"


def test_magicmock_endpoint_doc_safe() -> None:
    """Non-string __doc__ (MagicMock) must not break description extraction."""
    from unittest.mock import MagicMock

    endpoint = MagicMock()
    # Instance attribute that is truthy but not a str (old code would fail on .strip()).
    endpoint.__doc__ = MagicMock()

    decl = RouteDeclaration(
        path="/api/plugins/session_gated/{plugin_id}/enable",
        endpoint=endpoint,
        methods=("POST",),
        name="api_enable_plugin",
        auth_policy=AuthPolicy.SESSION_GATED,
        owner="api.plugins",
    )
    ops = declaration_to_operations(decl)
    assert len(ops) == 1
    assert isinstance(ops[0].description, str)
    assert ops[0].description == "POST /api/plugins/session_gated/{plugin_id}/enable"
