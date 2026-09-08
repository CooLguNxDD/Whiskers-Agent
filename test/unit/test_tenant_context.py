"""Schema-regression tests for Tenant model and tenant_id columns, plus tenant context propagation tests."""

import pytest
from unittest.mock import AsyncMock
from contextvars import ContextVar

from db_layer.models import (
    Tenant,
    ApiKey,
    ApiKeyScopePreset,
    WorkflowExecution,
    ToolCallEvent,
)
from core.context import current_tenant_id
from oauth.oauth_service import OAuthService, OAuthService_FastMCPProvider, InvalidTokenError
from core.api_key_management.scopes import resolve_api_key_scopes, is_allowed
from utils.server_config import OAUTH_VALID_SCOPES


def test_tenant_model_schema_exists():
    """Assert Tenant model exists and has correct columns."""
    assert Tenant is not None
    columns = Tenant.__table__.columns
    assert "id" in columns
    assert "name" in columns
    assert "slug" in columns
    assert "is_active" in columns
    assert "created_at" in columns


def test_related_models_have_tenant_id():
    """Assert multi-tenant models expose tenant_id (incl. tool_call_events)."""
    models = [
        ApiKey,
        ApiKeyScopePreset,
        WorkflowExecution,
        ToolCallEvent,
    ]
    for model in models:
        assert "tenant_id" in model.__table__.columns, f"tenant_id not found in {model.__name__}"


def test_tenant_context_default_value():
    """Assert current_tenant_id defaults to 1."""
    # We inspect the ContextVar default value
    from core.context._registries import current_tenant_id as raw_var
    assert raw_var.get() == 1


@pytest.mark.asyncio
async def test_jwt_tenant_context(monkeypatch):
    """Assert JWT payload with ocat_tenant sets current_tenant_id."""
    mock_svc = AsyncMock(spec=OAuthService)
    mock_svc.validate_token.return_value = {
        "client_id": "test_client",
        "scopes": ["admin"],
        "exp": 9999999999,
        "ocat_tenant": 7,
    }

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=list(OAUTH_VALID_SCOPES),
    )

    current_tenant_id.set(99)
    result = await provider.load_access_token("valid_jwt")
    assert result is not None
    assert current_tenant_id.get() == 7


@pytest.mark.asyncio
async def test_jwt_tenant_context_default(monkeypatch):
    """Assert JWT payload missing ocat_tenant defaults current_tenant_id to 1."""
    mock_svc = AsyncMock(spec=OAuthService)
    mock_svc.validate_token.return_value = {
        "client_id": "test_client",
        "scopes": ["admin"],
        "exp": 9999999999,
    }

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=list(OAUTH_VALID_SCOPES),
    )

    current_tenant_id.set(99)
    result = await provider.load_access_token("valid_jwt")
    assert result is not None
    assert current_tenant_id.get() == 1


@pytest.mark.asyncio
async def test_api_key_tenant_context(monkeypatch):
    """Assert API key lookup with tenant_id sets current_tenant_id."""
    mock_svc = AsyncMock(spec=OAuthService)
    mock_svc.validate_token.side_effect = InvalidTokenError("Invalid JWT")

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=list(OAUTH_VALID_SCOPES),
    )

    async def mock_lookup(token):
        return {
            "key_id": "ak_abc",
            "subject": "admin",
            "expires_at": None,
            "scopes": list(OAUTH_VALID_SCOPES),
            "tenant_id": 3,
        }

    monkeypatch.setattr("db_layer.api_key_store.lookup_active_by_token", mock_lookup)

    current_tenant_id.set(99)
    result = await provider.load_access_token("octk_valid_token_123")
    assert result is not None
    assert current_tenant_id.get() == 3


@pytest.mark.asyncio
async def test_api_key_tenant_context_default(monkeypatch):
    """Assert API key lookup missing tenant_id defaults current_tenant_id to 1."""
    mock_svc = AsyncMock(spec=OAuthService)
    mock_svc.validate_token.side_effect = InvalidTokenError("Invalid JWT")

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=list(OAUTH_VALID_SCOPES),
    )

    async def mock_lookup(token):
        return {
            "key_id": "ak_abc",
            "subject": "admin",
            "expires_at": None,
            "scopes": list(OAUTH_VALID_SCOPES),
        }

    monkeypatch.setattr("db_layer.api_key_store.lookup_active_by_token", mock_lookup)

    current_tenant_id.set(99)
    result = await provider.load_access_token("octk_valid_token_123")
    assert result is not None
    assert current_tenant_id.get() == 1


def test_tenant_context_guardrails():
    """Assert resolve_api_key_scopes and is_allowed are unaffected by tenant value."""
    row_with_tenant = {"scopes": ["plugin:foo"], "tenant_id": 99}
    row_without_tenant = {"scopes": ["plugin:foo"]}
    assert resolve_api_key_scopes(row_with_tenant) == resolve_api_key_scopes(row_without_tenant)

    required = {"plugin:foo"}
    current_tenant_id.set(1)
    allowed_1 = is_allowed(["plugin:foo"], required)

    current_tenant_id.set(99)
    allowed_99 = is_allowed(["plugin:foo"], required)

    assert allowed_1 is True
    assert allowed_99 is True
