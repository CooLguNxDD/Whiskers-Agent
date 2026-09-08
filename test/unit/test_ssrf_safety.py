"""Unit tests for SSRF protection utilities in core/proxy/ssrf_safety.py."""

import asyncio
import ipaddress
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.proxy.ssrf_safety import (
    _is_unsafe_ip,
    _resolve_safe_ip,
    _is_safe_url,
    _SSRFSafeTransport,
)


def test_is_unsafe_ip():
    # Loopback
    assert _is_unsafe_ip(ipaddress.ip_address("127.0.0.1")) is True
    assert _is_unsafe_ip(ipaddress.ip_address("::1")) is True

    # Private ranges
    assert _is_unsafe_ip(ipaddress.ip_address("10.0.0.1")) is True
    assert _is_unsafe_ip(ipaddress.ip_address("192.168.1.100")) is True
    assert _is_unsafe_ip(ipaddress.ip_address("172.16.0.1")) is True

    # Unspecified
    assert _is_unsafe_ip(ipaddress.ip_address("0.0.0.0")) is True
    assert _is_unsafe_ip(ipaddress.ip_address("::")) is True

    # Link-local
    assert _is_unsafe_ip(ipaddress.ip_address("169.254.169.254")) is True
    assert _is_unsafe_ip(ipaddress.ip_address("fe80::1")) is True

    # IPv4-mapped IPv6 (which unwraps to 127.0.0.1 and 169.254.169.254)
    assert _is_unsafe_ip(ipaddress.ip_address("::ffff:127.0.0.1")) is True
    assert _is_unsafe_ip(ipaddress.ip_address("::ffff:a9fe:a9fe")) is True  # 169.254.169.254

    # Public/safe IP
    assert _is_unsafe_ip(ipaddress.ip_address("93.184.216.34")) is False  # example.com
    assert _is_unsafe_ip(ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946")) is False


@pytest.mark.asyncio
async def test_resolve_safe_ip_literal():
    # Safe literal
    assert await _resolve_safe_ip("93.184.216.34") == "93.184.216.34"
    # Unsafe literal
    assert await _resolve_safe_ip("127.0.0.1") is None
    # Invalid IP literal (falls through to DNS resolution test)
    # but if DNS resolution fails, it returns None
    with patch("asyncio.get_running_loop") as mock_loop_get:
        mock_loop = MagicMock()
        mock_loop.getaddrinfo = AsyncMock(side_effect=Exception("DNS failure"))
        mock_loop_get.return_value = mock_loop
        assert await _resolve_safe_ip("not-a-valid-ip") is None


@pytest.mark.asyncio
async def test_resolve_safe_ip_dns_resolution():
    with patch("asyncio.get_running_loop") as mock_loop_get:
        mock_loop = MagicMock()
        mock_loop_get.return_value = mock_loop

        # 1. Hostname resolves to safe public IP
        # getaddrinfo returns list of tuples: (family, type, proto, canonname, sockaddr)
        # sockaddr for IPv4 is (ip, port)
        mock_loop.getaddrinfo = AsyncMock(return_value=[
            (2, 1, 6, "", ("93.184.216.34", 80))
        ])
        assert await _resolve_safe_ip("example.com") == "93.184.216.34"

        # 2. Hostname resolves to unsafe IP
        mock_loop.getaddrinfo = AsyncMock(return_value=[
            (2, 1, 6, "", ("127.0.0.1", 80))
        ])
        assert await _resolve_safe_ip("localhost") is None

        # 3. Hostname resolves to mixed IPs (one unsafe) -> should reject completely (return None)
        mock_loop.getaddrinfo = AsyncMock(return_value=[
            (2, 1, 6, "", ("93.184.216.34", 80)),
            (2, 1, 6, "", ("10.0.0.1", 80))
        ])
        assert await _resolve_safe_ip("mixed.example.com") is None

        # 4. Hostname DNS resolution times out -> fails closed (returns None)
        mock_loop.getaddrinfo = AsyncMock(side_effect=asyncio.TimeoutError)
        assert await _resolve_safe_ip("slow.example.com") is None



@pytest.mark.asyncio
async def test_is_safe_url():
    # Allow override bypass
    with patch.dict(os.environ, {"CAT_ALLOW_LOCAL_PROXIES": "1"}):
        assert await _is_safe_url("http://localhost:10000") is True

    # Without the dev override, missing / empty hostname must be rejected
    with patch.dict(os.environ, {"CAT_ALLOW_LOCAL_PROXIES": ""}, clear=False):
        # Missing hostname
        assert await _is_safe_url("http://") is False
        assert await _is_safe_url("not-a-url") is False

        # Delegates to _resolve_safe_ip
        with patch("core.proxy.ssrf_safety._resolve_safe_ip", AsyncMock(return_value="93.184.216.34")):
            assert await _is_safe_url("https://example.com/path") is True

        with patch("core.proxy.ssrf_safety._resolve_safe_ip", AsyncMock(return_value=None)):
            assert await _is_safe_url("https://localhost/path") is False


@pytest.mark.asyncio
async def test_ssrf_safe_transport_handle_request_safe():
    transport = _SSRFSafeTransport()
    mock_response = httpx.Response(200)

    # Ensure the dev-mode bypass is off so SSRF validation runs
    with patch.dict(os.environ, {"CAT_ALLOW_LOCAL_PROXIES": ""}, clear=False), \
         patch("core.proxy.ssrf_safety._resolve_safe_ip", AsyncMock(return_value="93.184.216.34")), \
         patch.object(httpx.AsyncHTTPTransport, "handle_async_request", AsyncMock(return_value=mock_response)) as mock_super:
        request = httpx.Request("GET", "https://example.com/foo")
        res = await transport.handle_async_request(request)
        assert res is mock_response

        # Verify request was rewritten: host pinned to safe IP, SNI preserved
        sent_request = mock_super.call_args[0][0]
        assert sent_request.url.host == "93.184.216.34"
        assert sent_request.extensions.get("sni_hostname") == "example.com"


@pytest.mark.asyncio
async def test_ssrf_safe_transport_handle_request_unsafe():
    transport = _SSRFSafeTransport()

    with patch.dict(os.environ, {"CAT_ALLOW_LOCAL_PROXIES": ""}, clear=False), \
         patch("core.proxy.ssrf_safety._resolve_safe_ip", AsyncMock(return_value=None)):
        request = httpx.Request("GET", "https://localhost/foo")
        with pytest.raises(httpx.ConnectError, match="resolves to a restricted address"):
            await transport.handle_async_request(request)


@pytest.mark.asyncio
async def test_ssrf_safe_transport_handle_request_bypass():
    transport = _SSRFSafeTransport()
    request = httpx.Request("GET", "https://localhost/foo")
    mock_response = httpx.Response(200)

    with patch.dict(os.environ, {"CAT_ALLOW_LOCAL_PROXIES": "1"}), \
         patch.object(httpx.AsyncHTTPTransport, "handle_async_request", AsyncMock(return_value=mock_response)) as mock_super:
        res = await transport.handle_async_request(request)
        assert res is mock_response

        # Request is untouched
        sent_request = mock_super.call_args[0][0]
        assert sent_request.url.host == "localhost"
        assert "sni_hostname" not in sent_request.extensions
