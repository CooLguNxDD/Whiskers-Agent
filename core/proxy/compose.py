"""FastMCP composition logic for Whiskers Agent upstream proxies."""

import logging
from typing import Any
import httpx

from fastmcp import FastMCP
from fastmcp.server import create_proxy
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.server.providers.proxy import ProxyClient
from fastmcp.server.providers.fastmcp_provider import FastMCPProvider

logger = logging.getLogger("whiskers.plugins")


class RelayAuth(httpx.Auth):
    """Custom httpx.Auth that retrieves fresh tokens from the ExternalOAuthRelay.
    
    A proxy is not a plugin, but gets a synthetic manifest to reuse ExternalOAuthRelay.
    """
    requires_response_body = False

    def __init__(self, plugin_id: str, provider: str = "upstream", auth_header: str | None = None):
        """Initialize RelayAuth with plugin id, provider, and custom auth header."""
        self.plugin_id = plugin_id
        self.provider = provider
        self.auth_header = auth_header or "Authorization"

    async def async_auth_flow(self, request: httpx.Request):
        """Asynchronous HTTPX auth flow to inject credentials into the request header."""
        from core.context import oauth_relay
        if oauth_relay is None:
            logger.error("RelayAuth: ExternalOAuthRelay singleton not available in core.context")
            yield request
            return

        try:
            token = await oauth_relay.get_token(self.plugin_id, self.provider)
            # Default to Authorization: Bearer <token>
            if self.auth_header.lower() == "authorization":
                request.headers[self.auth_header] = f"Bearer {token}"
            else:
                request.headers[self.auth_header] = token
        except Exception as e:
            logger.exception("RelayAuth: Failed to retrieve token from ExternalOAuthRelay: %s", e)
        
        yield request


def build_proxy(
    name: str,
    transport: str,
    url: str,
    auth_mode: str = "none",
    bearer_token: str | None = None,
    oauth_config: dict | None = None,
) -> FastMCPProvider:
    """Build a FastMCPProvider wrapping a proxy client to the upstream server."""
    logger.info(
        f"Building upstream proxy: name={name}, transport={transport}, url={url}, "
        f"auth_mode={auth_mode}, has_bearer={bool(bearer_token)}, has_oauth={bool(oauth_config)}"
    )
    
    auth_obj = None
    if auth_mode == "bearer" and bearer_token:
        auth_obj = bearer_token
    elif auth_mode == "oauth" and oauth_config:
        plugin_id = f"proxy_{name}"
        auth_header = oauth_config.get("auth_header") or "Authorization"
        auth_obj = RelayAuth(plugin_id=plugin_id, provider="upstream", auth_header=auth_header)

    if transport.lower() == "sse" or url.endswith("/sse"):
        client_transport = SSETransport(url=url, auth=auth_obj)
    else:
        client_transport = StreamableHttpTransport(url=url, auth=auth_obj)

    proxy_client = ProxyClient(client_transport, name=name)

    # create_proxy is the non-deprecated replacement for FastMCP.as_proxy()
    proxy_server = create_proxy(proxy_client, name=name)

    provider = FastMCPProvider(proxy_server)
    return provider


def mount_proxy(mcp: FastMCP, provider: FastMCPProvider, name: str) -> None:
    """Mount the proxy provider onto the core MCP instance under the specified namespace."""
    logger.info(f"Mounting proxy under namespace: {name}")
    mcp.add_provider(provider, namespace=name)


def _find_mounted_provider(
    mcp: FastMCP,
    handle: FastMCPProvider | None,
    *,
    namespace: str | None = None,
):
    """Locate the live providers-list entry for a proxy handle or namespace."""
    providers = getattr(mcp, "providers", None)
    if not isinstance(providers, list):
        return None

    for entry in providers:
        if handle is not None and entry is handle:
            return entry
        inner = getattr(entry, "_inner", None)
        if handle is not None and inner is handle:
            return entry
        if namespace:
            for transform in getattr(entry, "transforms", ()) or ():
                prefix = getattr(transform, "_prefix", None)
                if prefix == namespace:
                    return entry
    return None


def unmount_proxy(
    mcp: FastMCP,
    handle: FastMCPProvider | None,
    *,
    namespace: str | None = None,
) -> None:
    """Best-effort live unmount of a proxy provider from the mcp instance.

    NOTE: FastMCP 3.3.1 has no public removal API (no remove_provider()).
    Direct list manipulation on mcp.providers is the only option.
    """
    logger.info(
        "Attempting live unmount of proxy provider: %s (namespace=%s)",
        handle, namespace,
    )
    if handle is None and not namespace:
        logger.warning("unmount_proxy: no handle or namespace provided.")
        return

    target = _find_mounted_provider(mcp, handle, namespace=namespace)
    providers = getattr(mcp, "providers", None)
    if not isinstance(providers, list):
        logger.error("mcp.providers is not accessible or not a list. Cannot perform live unmount.")
        return

    if target is not None:
        providers.remove(target)
        logger.info("Successfully removed proxy provider from live registry.")
    else:
        logger.warning("Proxy provider handle not found in live registry.")
