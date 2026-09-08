"""Unit tests for the HTTPX client lifecycle management on teardown."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from oauth.oauth_relay import ExternalOAuthRelay
from core.plugin_loader.plugin_auth_registry import PluginAuthRegistry
from core.bootstrap import run_teardown, BootContext


@pytest.mark.asyncio
async def test_external_oauth_relay_aclose():
    """Assert that ExternalOAuthRelay.aclose() closes the underlying http client."""
    mock_vault = MagicMock()
    relay = ExternalOAuthRelay(vault=mock_vault, plugin_manifests={})
    
    mock_http = AsyncMock()
    relay._http = mock_http
    
    await relay.aclose()
    mock_http.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_plugin_auth_registry_aclose():
    """Assert that PluginAuthRegistry.aclose() closes the underlying http client."""
    mock_registry = MagicMock()
    auth_registry = PluginAuthRegistry(registry=mock_registry)
    
    mock_http = AsyncMock()
    auth_registry._http = mock_http
    
    await auth_registry.aclose()
    mock_http.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_teardown_closes_clients():
    """Assert that run_teardown() awaits both relay and auth aclose() methods."""
    mock_registry = MagicMock()
    
    # Mock life cycle teardown_plugins
    mock_registry.lifecycle.teardown_plugins = AsyncMock()
    
    # Mock relay and auth instances with async aclose methods
    mock_relay = MagicMock()
    mock_relay.aclose = AsyncMock()
    mock_registry._relay = mock_relay
    
    mock_auth = MagicMock()
    mock_auth.aclose = AsyncMock()
    mock_registry.auth = mock_auth
    
    # Construct BootContext
    ctx = BootContext(
        registry=mock_registry,
        mcp=MagicMock(),
        db_available=False
    )
    
    await run_teardown(ctx)
    
    mock_relay.aclose.assert_awaited_once()
    mock_auth.aclose.assert_awaited_once()
    mock_registry.lifecycle.teardown_plugins.assert_awaited_once()


@pytest.mark.asyncio
async def test_oauth_relay_no_bare_httpx_client():
    """Assert that oauth_relay.py does not construct a bare httpx.AsyncClient()."""
    import inspect
    import oauth.oauth_relay as relay_module

    source = inspect.getsource(relay_module)
    assert "httpx.AsyncClient(" not in source


@pytest.mark.asyncio
async def test_upsert_manifest_rejects_unsafe_url():
    """Assert that upsert_manifest rejects manifests with SSRF-unsafe URLs."""
    from unittest.mock import patch
    mock_vault = MagicMock()
    relay = ExternalOAuthRelay(vault=mock_vault, plugin_manifests={})

    unsafe_manifest = {
        "name": "unsafe_plugin",
        "token_url": "http://127.0.0.1:8000/token",
        "authorization_url": "https://example.com/auth"
    }

    with patch("core.proxy.ssrf_safety._is_safe_url", AsyncMock(return_value=False)):
        with pytest.raises(ValueError, match="Unsafe URL"):
            await relay.upsert_manifest("unsafe_plugin", unsafe_manifest)

