"""Unit tests for FastMCP provider API key authentication fallback.

Covers valid/invalid tokens, non-octk tokens, and valid JWT regression cases.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from mcp.server.auth.provider import AccessToken
from oauth.oauth_service import OAuthService, OAuthService_FastMCPProvider, InvalidTokenError
from utils.server_config import OAUTH_VALID_SCOPES


@pytest.mark.asyncio
async def test_api_key_auth_valid_token(monkeypatch):
    """Test lookup succeeds for a valid active API key token with no expiration."""
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

    token = "octk_valid_token_123"
    result = await provider.load_access_token(token)

    assert result is not None
    assert isinstance(result, AccessToken)
    assert result.token == token
    assert result.client_id == "api-key:ak_abc"
    assert result.scopes == list(OAUTH_VALID_SCOPES)
    assert result.expires_at is None

    # Verify token is NOT cached so revocation/deletion takes effect immediately
    assert provider._access_tokens.get(token) is None


@pytest.mark.asyncio
async def test_api_key_auth_valid_token_with_expiry(monkeypatch):
    """Test lookup succeeds for an API key token with expiration."""
    mock_svc = AsyncMock(spec=OAuthService)
    mock_svc.validate_token.side_effect = InvalidTokenError("Invalid JWT")

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=list(OAUTH_VALID_SCOPES),
    )

    expiry_dt = datetime(2030, 1, 1, tzinfo=timezone.utc)

    async def mock_lookup(token):
        return {
            "key_id": "ak_xyz",
            "subject": "developer",
            "expires_at": expiry_dt,
            "scopes": list(OAUTH_VALID_SCOPES),
        }

    monkeypatch.setattr("db_layer.api_key_store.lookup_active_by_token", mock_lookup)

    token = "octk_expiry_token"
    result = await provider.load_access_token(token)

    assert result is not None
    assert isinstance(result, AccessToken)
    assert result.token == token
    assert result.client_id == "api-key:ak_xyz"
    assert result.scopes == list(OAUTH_VALID_SCOPES)
    assert result.expires_at == int(expiry_dt.timestamp())


@pytest.mark.asyncio
async def test_api_key_auth_revoked_expired_token(monkeypatch):
    """Test lookup returns None for an unknown/revoked/expired octk_ token."""
    mock_svc = AsyncMock(spec=OAuthService)
    mock_svc.validate_token.side_effect = InvalidTokenError("Invalid JWT")

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=list(OAUTH_VALID_SCOPES),
    )

    async def mock_lookup(token):
        return None

    monkeypatch.setattr("db_layer.api_key_store.lookup_active_by_token", mock_lookup)

    token = "octk_revoked_token"
    result = await provider.load_access_token(token)

    assert result is None


@pytest.mark.asyncio
async def test_api_key_auth_non_octk_invalid_token(monkeypatch):
    """Test that a non-octk token bypasses the API key store lookup entirely."""
    mock_svc = AsyncMock(spec=OAuthService)
    mock_svc.validate_token.side_effect = InvalidTokenError("Invalid JWT")

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=list(OAUTH_VALID_SCOPES),
    )

    def sentinel_raise(*args, **kwargs):
        raise AssertionError("lookup_active_by_token was unexpectedly called")

    monkeypatch.setattr("db_layer.api_key_store.lookup_active_by_token", sentinel_raise)

    token = "not_octk_token"
    result = await provider.load_access_token(token)

    assert result is None


@pytest.mark.asyncio
async def test_api_key_auth_regression_valid_jwt(monkeypatch):
    """Test that a valid JWT token returns normally and does not invoke fallback lookup."""
    mock_svc = AsyncMock(spec=OAuthService)
    mock_svc.validate_token.return_value = {
        "client_id": "jwt_client_123",
        "scopes": ["whiskers"],
        "exp": 1893456000,
        "org_id": "custom-org",
    }

    provider = OAuthService_FastMCPProvider(
        service=mock_svc,
        base_url="http://localhost:10000",
        valid_scopes=list(OAUTH_VALID_SCOPES),
    )

    def sentinel_raise(*args, **kwargs):
        raise AssertionError("lookup_active_by_token was unexpectedly called")

    monkeypatch.setattr("db_layer.api_key_store.lookup_active_by_token", sentinel_raise)

    token = "octk_some_token"  # even with octk_ prefix, if validate_token succeeds, it bypasses lookup
    result = await provider.load_access_token(token)

    assert result is not None
    assert isinstance(result, AccessToken)
    assert result.token == token
    assert result.client_id == "jwt_client_123"
    assert result.scopes == ["whiskers"]
    assert result.expires_at == 1893456000
