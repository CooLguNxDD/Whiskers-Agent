"""SSRF (server-side request forgery) protection for outbound proxy HTTP requests.
Split out of core/proxy/proxy_manager.py (Phase 5 modularity refactor)."""
import asyncio
import os
import ipaddress
from urllib.parse import urlparse
from typing import Optional
import httpx


def _is_unsafe_ip(ip_obj) -> bool:
    """Return True if ip_obj is private/loopback/unspecified/link-local.
    Unwraps IPv4-mapped IPv6 addresses (e.g. ::ffff:127.0.0.1) first, since dual-stack
    sockets transparently route those to their IPv4 target, bypassing a check that only
    looks at the IPv6 representation."""
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        ip_obj = ip_obj.ipv4_mapped
    return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_unspecified or ip_obj.is_link_local


async def _resolve_safe_ip(hostname: str) -> Optional[str]:
    """Resolve hostname to a validated public IP, or None if it's a restricted literal IP
    or every resolved address is restricted. Used both for the upfront `_is_safe_url` check
    and to pin connections in `_SSRFSafeTransport`."""
    try:
        ip_obj = ipaddress.ip_address(hostname)
        return None if _is_unsafe_ip(ip_obj) else str(ip_obj)
    except ValueError:
        pass

    try:
        # Avoid blocking the event loop with synchronous DNS resolution.
        # Bound DNS lookup to prevent slow/hostile resolvers exhausting the default threadpool.
        loop = asyncio.get_running_loop()
        addr_info = await asyncio.wait_for(loop.getaddrinfo(hostname, None), timeout=2.0)
    except Exception:
        return None

    resolved_ips = []
    for result in addr_info:
        ip_obj = ipaddress.ip_address(result[4][0])
        if _is_unsafe_ip(ip_obj):
            return None
        resolved_ips.append(str(ip_obj))
    return resolved_ips[0] if resolved_ips else None


async def _is_safe_url(url: str) -> bool:
    """Return True if the URL's hostname resolves only to safe public IPs.
    Can be overridden by CAT_ALLOW_LOCAL_PROXIES=1."""
    if os.environ.get("CAT_ALLOW_LOCAL_PROXIES") == "1":
        return True

    hostname = urlparse(url).hostname
    if not hostname:
        return False

    return await _resolve_safe_ip(hostname) is not None


class _SSRFSafeTransport(httpx.AsyncHTTPTransport):
    """HTTP transport that re-resolves, validates, and pins the destination IP for every
    request it handles, including redirect hops (httpx re-invokes the transport for each
    hop in `_send_handling_redirects`). This closes the gaps a one-time `_is_safe_url`
    pre-check leaves open: a redirect to an internal address, a dynamically discovered URL
    (e.g. an OAuth metadata field) that was never pre-checked, and DNS rebinding between the
    pre-check and the actual connection (TOCTOU) — since the IP we connect to is the same one
    we just validated."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Handle async request after performing SSRF validations on destination host/IP."""
        if os.environ.get("CAT_ALLOW_LOCAL_PROXIES") != "1":
            hostname = request.url.host
            safe_ip = await _resolve_safe_ip(hostname)
            if not safe_ip:
                raise httpx.ConnectError(
                    f"URL '{request.url}' resolves to a restricted address and cannot be accessed."
                )
            # Pin the connection to the validated IP; keep the original hostname for TLS SNI.
            request.extensions["sni_hostname"] = hostname
            request.url = request.url.copy_with(host=safe_ip)
        return await super().handle_async_request(request)


def _safe_async_client(**kwargs) -> httpx.AsyncClient:
    """httpx.AsyncClient pre-wired with the SSRF-safe transport, used for all proxy discovery requests."""
    return httpx.AsyncClient(transport=_SSRFSafeTransport(), **kwargs)
