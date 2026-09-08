"""Unit tests for FastMCP composition and proxy setup logic."""

import pytest
from unittest.mock import patch, MagicMock
from core.proxy.compose import build_proxy, mount_proxy, unmount_proxy, RelayAuth


def test_build_proxy_none():
    with patch("core.proxy.compose.StreamableHttpTransport") as mock_http, \
         patch("core.proxy.compose.ProxyClient") as mock_client, \
         patch("core.proxy.compose.create_proxy") as mock_create_proxy, \
         patch("core.proxy.compose.FastMCPProvider") as mock_provider:

        mock_provider_instance = MagicMock()
        mock_provider.return_value = mock_provider_instance

        res = build_proxy("test-none", "http", "http://localhost/api", "none")

        mock_http.assert_called_once_with(url="http://localhost/api", auth=None)
        mock_client.assert_called_once_with(mock_http.return_value, name="test-none")
        mock_create_proxy.assert_called_once_with(mock_client.return_value, name="test-none")
        mock_provider.assert_called_once_with(mock_create_proxy.return_value)
        assert res == mock_provider_instance


def test_build_proxy_bearer():
    with patch("core.proxy.compose.StreamableHttpTransport") as mock_http, \
         patch("core.proxy.compose.ProxyClient") as mock_client, \
         patch("core.proxy.compose.create_proxy") as mock_create_proxy, \
         patch("core.proxy.compose.FastMCPProvider") as mock_provider:

        build_proxy("test-bearer", "http", "http://localhost/api", "bearer", "my-token")
        mock_http.assert_called_once_with(url="http://localhost/api", auth="my-token")


def test_build_proxy_oauth():
    with patch("core.proxy.compose.StreamableHttpTransport") as mock_http, \
         patch("core.proxy.compose.ProxyClient") as mock_client, \
         patch("core.proxy.compose.create_proxy") as mock_create_proxy, \
         patch("core.proxy.compose.FastMCPProvider") as mock_provider:

        oauth_config = {"auth_header": "X-Auth-Token", "client_id": "test-client"}
        build_proxy("test-oauth", "http", "http://localhost/api", "oauth", oauth_config=oauth_config)

        # Should create a RelayAuth instance and pass it to StreamableHttpTransport
        mock_http.assert_called_once()
        called_args, called_kwargs = mock_http.call_args
        auth_obj = called_kwargs["auth"]
        assert isinstance(auth_obj, RelayAuth)
        assert auth_obj.plugin_id == "proxy_test-oauth"
        assert auth_obj.auth_header == "X-Auth-Token"


def test_build_proxy_sse():
    with patch("core.proxy.compose.SSETransport") as mock_sse, \
         patch("core.proxy.compose.ProxyClient") as mock_client, \
         patch("core.proxy.compose.create_proxy") as mock_create_proxy, \
         patch("core.proxy.compose.FastMCPProvider") as mock_provider:

        build_proxy("test-sse", "sse", "http://localhost/sse-endpoint", "none")
        mock_sse.assert_called_once_with(url="http://localhost/sse-endpoint", auth=None)


def test_mount_proxy():
    mock_mcp = MagicMock()
    mock_provider = MagicMock()
    
    mount_proxy(mock_mcp, mock_provider, "namespace-test")
    mock_mcp.add_provider.assert_called_once_with(mock_provider, namespace="namespace-test")


def test_unmount_proxy_success():
    mock_mcp = MagicMock()
    mock_provider = MagicMock()
    mock_mcp.providers = [mock_provider]
    
    unmount_proxy(mock_mcp, mock_provider)
    assert mock_provider not in mock_mcp.providers


def test_unmount_proxy_wrapped_provider():
    """FastMCP 3.x stores _WrappedProvider entries; match via _inner handle."""
    mock_mcp = MagicMock()
    inner = MagicMock()
    wrapped = MagicMock()
    wrapped._inner = inner
    wrapped.transforms = ()
    mock_mcp.providers = [wrapped]

    unmount_proxy(mock_mcp, inner)
    assert wrapped not in mock_mcp.providers


def test_unmount_proxy_by_namespace():
    mock_mcp = MagicMock()
    namespace_transform = MagicMock()
    namespace_transform._prefix = "notion"
    wrapped = MagicMock()
    wrapped._inner = MagicMock()
    wrapped.transforms = (namespace_transform,)
    mock_mcp.providers = [wrapped]

    unmount_proxy(mock_mcp, None, namespace="notion")
    assert wrapped not in mock_mcp.providers


def test_unmount_proxy_not_found():
    mock_mcp = MagicMock()
    mock_provider1 = MagicMock()
    mock_provider2 = MagicMock()
    mock_mcp.providers = [mock_provider1]
    
    unmount_proxy(mock_mcp, mock_provider2)
    assert mock_provider1 in mock_mcp.providers


def test_unmount_proxy_no_providers_list():
    mock_mcp = MagicMock(spec=[])
    mock_provider = MagicMock()
    
    # Should handle gracefully without throwing exception
    unmount_proxy(mock_mcp, mock_provider)
