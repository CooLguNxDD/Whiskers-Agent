"""ProxyManager for orchestrating upstream MCP server proxies."""

import asyncio
import logging
import re
import os
import ipaddress
from urllib.parse import urlparse
from typing import Any, List, Dict, Optional
from sqlalchemy import select, update

import core.context as ctx
from db_layer.connection import get_async_session
from core.proxy.models import ProxyServer
from core.proxy.compose import build_proxy, mount_proxy, unmount_proxy
from core.proxy import ssrf_safety

# Keep re-exports for test compatibility or other packages
_is_safe_url = ssrf_safety._is_safe_url
_SSRFSafeTransport = ssrf_safety._SSRFSafeTransport
_safe_async_client = ssrf_safety._safe_async_client


logger = logging.getLogger("whiskers")

# Scope helpers live in core.proxy.scopes (SSRF transport in ssrf_safety).
from core.proxy.scopes import (  # noqa: E402
    _clear_proxy_scopes,
    _register_proxy_scopes,
    _validate_proxy_name,
)
# Content-hash / plugins-row / OAuth-manifest registration (mount bookkeeping).
from core.proxy.proxy_registration import (  # noqa: E402
    _bump_tool_meta_version,
    _ensure_plugins_row,
    _persist_proxy_content_hash,
    _upsert_oauth_manifest,
    bump_tool_meta_version,
    ensure_plugins_row,
    persist_proxy_content_hash,
    upsert_oauth_manifest,
)

from core.interfaces import IProxyManager

_UNSET = object()  # sentinel distinguishing "not provided" from explicit None


class ProxyManager(IProxyManager):
    """Orchestrates upstream MCP servers: validation, persistence, mounting, and discovery."""

    def __init__(self):
        """Initialize the proxy manager with an empty active handles dictionary."""
        self._active_handles = {}  # name -> provider_handle


    def subscribe(self, events) -> None:
        events.on("system.boot_proxies", self._on_boot_proxies)

    async def _on_boot_proxies(self, *args, **kwargs) -> None:
        await self.load_persisted()

    async def get_oauth_status(self, name: str) -> str:
        """Check if a token exists for proxy_{name}."""
        from core.context import oauth_relay
        if not oauth_relay:
            return "not_connected"
        try:
            has_tok = await oauth_relay.has_token(f"proxy_{name}", "upstream")
            return "connected" if has_tok else "not_connected"
        except Exception:
            logger.debug("get_oauth_status failed for %s", name, exc_info=True)
            return "not_connected"

    async def discover_oauth_metadata(self, url: str, name: str) -> dict:
        """Perform RFC 8414 / SEP-985 discovery of OAuth endpoints from the upstream server."""

        if not await ssrf_safety._is_safe_url(url):
            raise ValueError(f"URL '{url}' is restricted and cannot be accessed.")
        
        authorize_url = None
        token_url = None
        registration_endpoint = None
        
        logger.info(f"Discovery: attempting OAuth metadata autodiscovery for {url}...")
        
        # 1. Try hitting the endpoint first to see if it gives WWW-Authenticate
        async with ssrf_safety._safe_async_client(timeout=10, follow_redirects=True) as client:
            try:
                resp = await client.get(url)
            except Exception as e:
                logger.debug(f"Discovery: error hitting endpoint {url}: {e}")
                resp = None
                 
        if resp is not None and resp.status_code == 401:
            www_auth = resp.headers.get("www-authenticate", "")
            logger.debug(f"Discovery: found WWW-Authenticate: {www_auth}")
            
            # Look for ResourceMetadata="..." or similar parameter
            match = re.search(r'ResourceMetadata="([^"]+)"', www_auth)
            if match:
                rm_url = match.group(1)
                logger.info(f"Discovery: found protected resource metadata link: {rm_url}")
                try:
                    async with ssrf_safety._safe_async_client(timeout=10, follow_redirects=True) as client:
                        rm_resp = await client.get(rm_url)
                        if rm_resp.status_code == 200:
                            rm_data = rm_resp.json()
                            auth_servers = rm_data.get("authorization_servers", [])
                            if auth_servers:
                                auth_server = auth_servers[0]
                                oasm_urls = [
                                    f"{auth_server.rstrip('/')}/.well-known/oauth-authorization-server",
                                    f"{auth_server.rstrip('/')}/.well-known/openid-configuration"
                                ]
                                for oasm_url in oasm_urls:
                                    oasm_resp = await client.get(oasm_url)
                                    if oasm_resp.status_code == 200:
                                        oasm_data = oasm_resp.json()
                                        authorize_url = oasm_data.get("authorization_endpoint")
                                        token_url = oasm_data.get("token_endpoint")
                                        registration_endpoint = oasm_data.get("registration_endpoint")
                                        logger.info(f"Discovery: discovered OAuth endpoints from protected resource server: auth={authorize_url}, token={token_url}")
                                        break
                except Exception as e:
                    logger.debug(f"Discovery: error parsing ResourceMetadata: {e}")
                     
        # 2. Fall back to standard well-known discovery candidates
        if not authorize_url or not token_url:
            try:
                parsed = urlparse(url)
                domain_root = f"{parsed.scheme}://{parsed.netloc}"
                
                well_known_candidates = [
                    f"{url.rstrip('/')}/.well-known/oauth-authorization-server",
                    f"{domain_root}/.well-known/oauth-authorization-server",
                    f"{url.rstrip('/')}/.well-known/openid-configuration",
                    f"{domain_root}/.well-known/openid-configuration",
                ]
                
                async with ssrf_safety._safe_async_client(timeout=10, follow_redirects=True) as client:
                    for candidate in well_known_candidates:
                        try:
                            logger.debug(f"Discovery: trying well-known candidate: {candidate}")
                            oasm_resp = await client.get(candidate)
                            if oasm_resp.status_code == 200:
                                oasm_data = oasm_resp.json()
                                authorize_url = oasm_data.get("authorization_endpoint")
                                token_url = oasm_data.get("token_endpoint")
                                registration_endpoint = oasm_data.get("registration_endpoint")
                                if authorize_url and token_url:
                                    logger.info(f"Discovery: successfully discovered metadata via {candidate}")
                                    break
                        except Exception as exc:
                            logger.debug("swallowed exception (non-fatal): %s", exc)
            except Exception as e:
                logger.debug(f"Discovery: error building candidate list: {e}")
                         
        if not authorize_url or not token_url:
            raise ValueError(
                "OAuth metadata autodiscovery failed. "
                "Upstream server does not expose standard OAuth metadata endpoints. "
                "Please specify Authorize URL and Token URL manually."
            )
             
        return {
            "authorize_url": authorize_url,
            "token_url": token_url,
            "registration_endpoint": registration_endpoint
        }

    async def _ensure_plugins_row(self, name: str) -> None:
        """Ensure a plugins table row exists for proxy_{name} to satisfy Vault FK constraints."""
        await ensure_plugins_row(name)

    async def _persist_proxy_content_hash(
        self,
        name: str,
        url: str,
        transport: str,
        auth_mode: str,
        custom_description: str | None = None,
        workspace_label: str | None = None,
    ) -> None:
        """Persist identity-tuple content hash on the proxy's plugins row (non-fatal)."""
        await persist_proxy_content_hash(
            name,
            url,
            transport,
            auth_mode,
            custom_description,
            workspace_label,
        )

    async def _upsert_oauth_manifest(self, proxy: ProxyServer) -> None:
        """Register synthetic OAuth manifest for a proxy row."""
        await upsert_oauth_manifest(proxy)

    async def _mount_proxy_row(self, proxy: ProxyServer, *, require_oauth: bool = True) -> bool:
        """Build, discover, mount, and emit tools for a proxy DB row. Returns True on success."""
        await self._upsert_oauth_manifest(proxy)

        bearer_token = None
        if proxy.auth_mode == "bearer":
            bearer_token = await ctx.vault.get(f"proxy_{proxy.name}", "bearer")

        if proxy.auth_mode == "oauth" and require_oauth:
            oauth_status = await self.get_oauth_status(proxy.name)
            if oauth_status != "connected":
                logger.info(
                    "ProxyManager: skipping mount of OAuth proxy %s — not connected yet.",
                    proxy.name,
                )
                return False

        provider = build_proxy(
            name=proxy.name,
            transport=proxy.transport,
            url=proxy.url,
            auth_mode=proxy.auth_mode,
            bearer_token=bearer_token,
            oauth_config=proxy.oauth_config,
        )
        proxy_server = provider.server

        try:
            tools = await proxy_server.list_tools()
            tool_count = len(tools)
        except Exception as ex:
            logger.warning("Error discovering tools for proxy %s: %s", proxy.name, ex)
            async with get_async_session() as session_write:
                await session_write.execute(
                    update(ProxyServer)
                    .where(ProxyServer.id == proxy.id)
                    .values(status="error", error_message=str(ex))
                )
                await session_write.commit()
            return False

        old_handle = self._active_handles.get(proxy.name)
        if old_handle:
            try:
                unmount_proxy(ctx.mcp, old_handle, namespace=proxy.name)
            except Exception as exc:
                logger.debug("ProxyManager: unmount of old handle for %s failed: %s", proxy.name, exc)

        mount_proxy(ctx.mcp, provider, proxy.name)
        self._active_handles[proxy.name] = provider
        _bump_tool_meta_version()

        _register_proxy_scopes(proxy.name)

        try:
            self._remove_proxy_routes(proxy.name)
            from core.plugin_loader.plugin_registry import get_registry
            registry = get_registry()
            registry.events.emit(
                "proxy.tools_discovered",
                {
                    "name": proxy.name,
                    "tools": tools,
                    "custom_description": proxy.custom_description,
                    "workspace_label": proxy.workspace_label,
                },
            )
        except Exception as ev_err:
            logger.debug("EventBus emit skipped: %s", ev_err)

        # After routes + mount: hide proxy tools when run_graph_unified is ON.
        try:
            from core.proxy_tools.tool_visibility import (
                hide_proxy_tools_if_gateway,
                reapply_hidden_for_plugin,
            )
            await hide_proxy_tools_if_gateway(proxy.name, tools)
            await reapply_hidden_for_plugin(f"proxy_{proxy.name}")
        except Exception as exc:
            logger.warning("ProxyManager: failed to reapply hidden state for %s: %s", proxy.name, exc)

        await self._persist_proxy_content_hash(
            proxy.name,
            proxy.url,
            proxy.transport,
            proxy.auth_mode,
            proxy.custom_description,
            proxy.workspace_label,
        )

        async with get_async_session() as session_write:
            await session_write.execute(
                update(ProxyServer)
                .where(ProxyServer.id == proxy.id)
                .values(status="active", tool_count=tool_count, error_message=None)
            )
            await session_write.commit()
        return True

    async def enable_proxy(self, name: str) -> None:
        """Re-mount a persisted proxy without deleting its DB row or vault secrets."""
        if name in self._active_handles:
            async with get_async_session() as session:
                await session.execute(
                    update(ProxyServer)
                    .where(ProxyServer.name == name)
                    .values(status="active", error_message=None)
                )
                await session.commit()
            return

        async with get_async_session() as session:
            proxy = (await session.execute(
                select(ProxyServer).where(ProxyServer.name == name)
            )).scalar_one_or_none()
            if not proxy:
                logger.warning("enable_proxy: Proxy '%s' not found in DB.", name)
                return

        await self._mount_proxy_row(proxy)

    def _remove_proxy_routes(self, name: str) -> int:
        """Drop route-registry contributions for a proxy plugin id."""
        plugin_id = f"proxy_{name}"
        try:
            from core.context import route_registry
            removed = route_registry.remove_plugin(plugin_id)
            if removed:
                logger.info(
                    "ProxyManager: removed %d route(s) for %s", removed, plugin_id,
                )
            return removed
        except Exception as exc:
            logger.warning(
                "ProxyManager: failed to remove routes for %s: %s", plugin_id, exc,
            )
            return 0

    async def disable_proxy(self, name: str) -> None:
        """Unmount a proxy and mark it inactive without deleting DB rows or revoking tokens."""
        handle = self._active_handles.get(name)
        try:
            unmount_proxy(ctx.mcp, handle, namespace=name)
        except Exception as u_err:
            logger.error("disable_proxy: unmount of %s failed: %s", name, u_err)
        self._active_handles.pop(name, None)
        _bump_tool_meta_version()
        self._remove_proxy_routes(name)
        _clear_proxy_scopes(name)

        async with get_async_session() as session:
            await session.execute(
                update(ProxyServer)
                .where(ProxyServer.name == name)
                .values(status="inactive")
            )
            await session.commit()

    async def load_persisted(self) -> None:
        """Boot-time re-mount of every active row in the proxy_servers table."""
        logger.info("ProxyManager: Loading persisted proxies...")
        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.status == "active")
            result = await session.execute(stmt)
            proxies = result.scalars().all()

        async def safe_mount(proxy: ProxyServer) -> None:
            try:
                await self._mount_proxy_row(proxy)
            except Exception as e:
                logger.exception("Failed to mount persisted proxy %s during boot: %s", proxy.name, e)

        if proxies:
            await asyncio.gather(*(safe_mount(p) for p in proxies))

    async def get_proxy_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Return a single proxy row as a public dict, or None if missing."""
        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.name == name)
            p = (await session.execute(stmt)).scalar_one_or_none()
        if p is None:
            return None
        return {
            "id": str(p.id),
            "name": p.name,
            "transport": p.transport,
            "url": p.url,
            "status": p.status,
            "authMode": p.auth_mode,
            "toolCount": p.tool_count,
            "errorMessage": p.error_message,
            "oauthConfig": p.oauth_config,
            "customDescription": p.custom_description,
            "workspaceLabel": p.workspace_label,
        }

    async def list_proxies(self) -> List[Dict[str, Any]]:
        """List all proxies from the database."""
        async with get_async_session() as session:
            stmt = select(ProxyServer).order_by(ProxyServer.name)
            result = await session.execute(stmt)
            proxies = result.scalars().all()
            
        async def fetch_oauth_status(p: ProxyServer) -> str:
            if p.auth_mode == "oauth":
                return await self.get_oauth_status(p.name)
            return "not_connected"

        oauth_statuses = []
        if proxies:
            oauth_statuses = await asyncio.gather(*(fetch_oauth_status(p) for p in proxies))

        res = []
        for i, p in enumerate(proxies):
            res.append({
                "id": str(p.id),
                "name": p.name,
                "transport": p.transport,
                "url": p.url,
                "status": p.status,
                "authMode": p.auth_mode,
                "oauthStatus": oauth_statuses[i] if oauth_statuses else "not_connected",
                "toolCount": p.tool_count,
                "errorMessage": p.error_message,
                "oauthConfig": p.oauth_config,
                "customDescription": p.custom_description,
                "workspaceLabel": p.workspace_label,
            })
        return res

    async def add_proxy(
        self,
        name: str,
        transport: str,
        url: str,
        auth_mode: str = "none",
        bearer_token: Optional[str] = None,
        oauth_config: Optional[dict] = None,
        client_secret: Optional[str] = None,
        custom_description: Optional[str] = None,
        workspace_label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add, persist, vault-store, mount, and discover a new proxy."""
        # Validation name regex
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise ValueError("Invalid name format. Only alphanumeric characters, dashes, and underscores allowed.")
            
        # Validation transport
        if transport not in ("http", "sse"):
            raise ValueError("Invalid transport. Must be 'http' or 'sse'.")
            
        # Validation URL scheme
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("URL must start with http:// or https://")

        if not await ssrf_safety._is_safe_url(url):
            raise ValueError(f"URL '{url}' is restricted and cannot be used as a proxy.")

        # Reject duplicate name early before any vault/manifest writes
        async with get_async_session() as session:
            existing = (await session.execute(
                select(ProxyServer).where(ProxyServer.name == name)
            )).scalar_one_or_none()
            if existing:
                raise ValueError(f"Proxy with name '{name}' already exists.")

        # 1. OAuth configurations and registration
        if auth_mode == "oauth":
            if not oauth_config:
                oauth_config = {}
                
            authorize_url = oauth_config.get("authorize_url")
            token_url = oauth_config.get("token_url")
            client_id = oauth_config.get("client_id")
            
            # Perform autodiscovery if any of these are missing
            if not authorize_url or not token_url or not client_id:
                try:
                    discovered = await self.discover_oauth_metadata(url, name)
                    if not authorize_url:
                        authorize_url = discovered["authorize_url"]
                    if not token_url:
                        token_url = discovered["token_url"]
                        
                    # Dynamic Client Registration if client_id is missing
                    if not client_id:
                        reg_endpoint = discovered.get("registration_endpoint")
                        if reg_endpoint:
                            from core.context import MCP_SERVER_URL
                            redirect_uri = f"{MCP_SERVER_URL}/oauth/plugin/upstream/callback"
                            
                            reg_payload = {
                                "client_name": f"Whiskers Agent ({name})",
                                "redirect_uris": [redirect_uri],
                                "response_types": ["code"],
                                "grant_types": ["authorization_code", "refresh_token"]
                            }
                            
                            async with ssrf_safety._safe_async_client(timeout=10) as client:
                                reg_resp = await client.post(reg_endpoint, json=reg_payload)
                                if reg_resp.status_code in (200, 201):
                                    reg_data = reg_resp.json()
                                    client_id = reg_data["client_id"]
                                    if reg_data.get("client_secret"):
                                        client_secret = reg_data["client_secret"]
                                    logger.info(f"Discovery: DCR success client_id={client_id}")
                                else:
                                    raise ValueError(f"Server rejected client registration: {reg_resp.text}")
                        else:
                            raise ValueError(
                                "Client ID is required. Upstream server does not support Dynamic Client Registration."
                            )
                except Exception as disc_err:
                    raise ValueError(f"OAuth Autodiscovery failed: {str(disc_err)}")

            oauth_config = {
                "authorize_url": authorize_url,
                "token_url": token_url,
                "client_id": client_id,
                "scopes": oauth_config.get("scopes", []),
                "pkce": oauth_config.get("pkce", "S256"),
                "auth_header": oauth_config.get("auth_header", "Authorization")
            }

            manifest = {
                "name": f"proxy_{name}",
                "version": "1.0.0",
                "tier": 1,
                "external_oauth": {
                    "upstream": {
                        "authorize_url": authorize_url,
                        "token_url": token_url,
                        "client_id": client_id,
                        "scopes": oauth_config.get("scopes", []),
                        "pkce": oauth_config.get("pkce", "S256"),
                        "redirect_path": "/oauth/plugin/upstream/callback",
                        "auth_header": oauth_config.get("auth_header", "Authorization")
                    }
                }
            }
            from core.context import oauth_relay
            if oauth_relay:
                await oauth_relay.upsert_manifest(f"proxy_{name}", manifest)
            
            # Persist OAuth client secret if present
            if client_secret:
                await self._ensure_plugins_row(name)
                await ctx.vault.set(f"proxy_{name}", "UPSTREAM_CLIENT_SECRET", client_secret)

        elif auth_mode == "bearer" and bearer_token:
            await self._ensure_plugins_row(name)
            await ctx.vault.set(f"proxy_{name}", "bearer", bearer_token)

        # 2. Persist in DB first
        async with get_async_session() as session:
            # Check for duplicate name
            stmt = select(ProxyServer).where(ProxyServer.name == name)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                raise ValueError(f"Proxy with name '{name}' already exists.")

            new_proxy = ProxyServer(
                name=name,
                transport=transport,
                url=url,
                status="inactive" if auth_mode == "oauth" else "active",
                has_auth=(auth_mode != "none"),
                auth_mode=auth_mode,
                oauth_config=oauth_config,
                tool_count=0,
                error_message=None,
                custom_description=custom_description,
                workspace_label=workspace_label,
            )
            session.add(new_proxy)
            await session.commit()
            
            # Fetch to return updated row
            await session.refresh(new_proxy)
            proxy_id = new_proxy.id

        # 3. Mount and discover if NOT oauth (since oauth needs token authorization first)
        tools = []
        tool_count = 0
        status = "inactive"
        error_message = None

        if auth_mode != "oauth":
            # Build proxy
            provider = build_proxy(
                name=name,
                transport=transport,
                url=url,
                auth_mode=auth_mode,
                bearer_token=bearer_token,
                oauth_config=oauth_config
            )

            # Discovery
            proxy_server = provider.server
            try:
                tools = await proxy_server.list_tools()
                tool_count = len(tools)
                status = "active"
            except Exception as e:
                logger.exception("Proxy discovery failed for %s", name)
                # Cleanup DB on failure
                async with get_async_session() as session:
                    stmt = select(ProxyServer).where(ProxyServer.name == name)
                    proxy = (await session.execute(stmt)).scalar_one_or_none()
                    if proxy:
                        await session.delete(proxy)
                        await session.commit()
                raise ValueError("Upstream MCP server connection failed during discovery.") from e

            # Mount live
            mount_proxy(ctx.mcp, provider, name)
            self._active_handles[name] = provider

            _register_proxy_scopes(name)

            # Emit event
            try:
                from core.plugin_loader.plugin_registry import get_registry
                registry = get_registry()
                registry.events.emit(
                    "proxy.tools_discovered",
                    {
                        "name": name,
                        "tools": tools,
                        "custom_description": custom_description,
                        "workspace_label": workspace_label,
                    },
                )
            except Exception as ev_err:
                logger.debug(f"EventBus emit skipped: {ev_err}")

            # After routes + mount: hide proxy tools when run_graph_unified is ON.
            try:
                from core.proxy_tools.tool_visibility import (
                    hide_proxy_tools_if_gateway,
                    reapply_hidden_for_plugin,
                )
                await hide_proxy_tools_if_gateway(name, tools)
                await reapply_hidden_for_plugin(f"proxy_{name}")
            except Exception as exc:
                logger.warning("ProxyManager: failed to reapply hidden state for %s: %s", name, exc)

            # Update DB with discovered values
            async with get_async_session() as session:
                await session.execute(
                    update(ProxyServer)
                        .where(ProxyServer.id == proxy_id)
                        .values(status=status, tool_count=tool_count)
                )
                await session.commit()

        # Identity hash on create (oauth inactive mounts still get a plugins row hash).
        await self._persist_proxy_content_hash(
            name, url, transport, auth_mode, custom_description, workspace_label
        )

        # Retrieve final row state
        oauth_status = "not_connected"
        if auth_mode == "oauth":
            oauth_status = await self.get_oauth_status(name)

        return {
            "id": str(proxy_id),
            "name": name,
            "transport": transport,
            "url": url,
            "status": status if auth_mode != "oauth" else "inactive",
            "authMode": auth_mode,
            "oauthStatus": oauth_status,
            "toolCount": tool_count,
            "errorMessage": error_message,
            "customDescription": custom_description,
            "workspaceLabel": workspace_label,
        }

    async def remove_proxy(self, name: str) -> Dict[str, Any]:
        """Delete from DB, vault secret, and attempt live unmount."""
        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.name == name)
            proxy = (await session.execute(stmt)).scalar_one_or_none()
            if not proxy:
                raise ValueError(f"Proxy '{name}' not found.")
            
            # Delete row
            await session.delete(proxy)
            await session.commit()

        # Delete from vault
        try:
            await ctx.vault.delete(f"proxy_{name}", "bearer")
        except Exception as v_err:
            logger.warning(f"Error deleting vault bearer token for proxy {name}: {v_err}")

        try:
            await ctx.vault.delete(f"proxy_{name}", "UPSTREAM_CLIENT_SECRET")
        except Exception as v_err:
            logger.warning(f"Error deleting vault client secret for proxy {name}: {v_err}")

        # Delete external oauth tokens
        from core.context import oauth_relay
        if oauth_relay:
            try:
                await oauth_relay.revoke_token(f"proxy_{name}", "upstream")
            except Exception as tok_err:
                logger.warning(f"Error revoking oauth tokens for proxy {name}: {tok_err}")

        # Delete corresponding plugins row
        from sqlalchemy import delete
        from db_layer.models import PluginModel

        async with get_async_session() as session:
            try:
                await session.execute(
                    delete(PluginModel).where(PluginModel.id == f"proxy_{name}")
                )
                await session.commit()
            except Exception as p_err:
                logger.debug(f"Error deleting plugins row for proxy_{name}: {p_err}")

        # Attempt live unmount
        restart_required = False
        handle = self._active_handles.get(name)
        try:
            unmount_proxy(ctx.mcp, handle, namespace=name)
            self._active_handles.pop(name, None)
        except Exception as u_err:
            logger.error("Live unmount of proxy %s failed: %s", name, u_err)
            restart_required = True
        _bump_tool_meta_version()
        self._remove_proxy_routes(name)
        _clear_proxy_scopes(name)

        return {"ok": True, "restartRequired": restart_required}

    async def test_proxy(self, name: str) -> Dict[str, Any]:
        """Re-connect, refresh toolCount, update status and errors in DB."""
        async with get_async_session() as session:
            stmt = select(ProxyServer).where(ProxyServer.name == name)
            proxy = (await session.execute(stmt)).scalar_one_or_none()
            if not proxy:
                raise ValueError(f"Proxy '{name}' not found.")

            # Register/upsert synthetic manifest if OAuth mode
            if proxy.auth_mode == "oauth" and proxy.oauth_config:
                manifest = {
                    "name": f"proxy_{proxy.name}",
                    "version": "1.0.0",
                    "tier": 1,
                    "external_oauth": {
                        "upstream": {
                            "authorize_url": proxy.oauth_config.get("authorize_url"),
                            "token_url": proxy.oauth_config.get("token_url"),
                            "client_id": proxy.oauth_config.get("client_id"),
                            "scopes": proxy.oauth_config.get("scopes", []),
                            "pkce": proxy.oauth_config.get("pkce", "S256"),
                            "redirect_path": "/oauth/plugin/upstream/callback",
                            "auth_header": proxy.oauth_config.get("auth_header", "Authorization")
                        }
                    }
                }
                from core.context import oauth_relay
                if oauth_relay:
                    await oauth_relay.upsert_manifest(f"proxy_{proxy.name}", manifest)

            # Get secrets
            bearer_token = None
            if proxy.auth_mode == "bearer":
                bearer_token = await ctx.vault.get(f"proxy_{name}", "bearer")

            # Rebuild proxy for testing
            try:
                provider = build_proxy(
                    name=proxy.name,
                    transport=proxy.transport,
                    url=proxy.url,
                    auth_mode=proxy.auth_mode,
                    bearer_token=bearer_token,
                    oauth_config=proxy.oauth_config
                )
                proxy_server = provider.server
                tools = await proxy_server.list_tools()
                
                # Connection success! Update DB
                proxy.status = "active"
                proxy.tool_count = len(tools)
                proxy.error_message = None
                await session.commit()

                # Perform best-effort unmount of old handle first if present
                old_handle = self._active_handles.get(name)
                if old_handle:
                    try:
                        unmount_proxy(ctx.mcp, old_handle, namespace=name)
                    except Exception as exc:
                        logger.debug("ProxyManager: unmount of old handle for %s failed: %s", name, exc)
                
                mount_proxy(ctx.mcp, provider, name)
                self._active_handles[name] = provider

                _register_proxy_scopes(name)

                # Emit event
                try:
                    self._remove_proxy_routes(name)
                    from core.plugin_loader.plugin_registry import get_registry
                    registry = get_registry()
                    registry.events.emit(
                        "proxy.tools_discovered",
                        {
                            "name": name,
                            "tools": tools,
                            "custom_description": proxy.custom_description,
                            "workspace_label": proxy.workspace_label,
                        },
                    )
                except Exception as ev_err:
                    logger.debug(f"EventBus emit skipped: {ev_err}")

                # After routes + mount: hide proxy tools when run_graph_unified is ON.
                try:
                    from core.proxy_tools.tool_visibility import (
                        hide_proxy_tools_if_gateway,
                        reapply_hidden_for_plugin,
                    )
                    await hide_proxy_tools_if_gateway(name, tools)
                    await reapply_hidden_for_plugin(f"proxy_{name}")
                except Exception as exc:
                    logger.warning("ProxyManager: failed to reapply hidden state for %s: %s", name, exc)

                oauth_status = "not_connected"
                if proxy.auth_mode == "oauth":
                    oauth_status = await self.get_oauth_status(name)

                return {
                    "id": str(proxy.id),
                    "name": proxy.name,
                    "transport": proxy.transport,
                    "url": proxy.url,
                    "status": proxy.status,
                    "authMode": proxy.auth_mode,
                    "oauthStatus": oauth_status,
                    "toolCount": proxy.tool_count,
                    "errorMessage": proxy.error_message,
                    "customDescription": proxy.custom_description,
                    "workspaceLabel": proxy.workspace_label,
                }
            except Exception as e:
                logger.exception("Proxy discovery failed for %s", name)
                # Connection failed! Update DB
                proxy.status = "error"
                proxy.error_message = "Connection failed during discovery."
                await session.commit()
                
                oauth_status = "not_connected"
                if proxy.auth_mode == "oauth":
                    oauth_status = await self.get_oauth_status(name)

                return {
                    "id": str(proxy.id),
                    "name": proxy.name,
                    "transport": proxy.transport,
                    "url": proxy.url,
                    "status": proxy.status,
                    "authMode": proxy.auth_mode,
                    "oauthStatus": oauth_status,
                    "toolCount": proxy.tool_count,
                    "errorMessage": proxy.error_message,
                    "customDescription": proxy.custom_description,
                    "workspaceLabel": proxy.workspace_label,
                }

    async def update_proxy_description(
        self,
        name: str,
        custom_description: str | None | object = _UNSET,
        workspace_label: str | None | object = _UNSET,
    ) -> Dict[str, Any]:
        """Update the proxy's custom description and/or workspace label and re-embed routes if active."""
        async with get_async_session() as session:
            proxy = (await session.execute(
                select(ProxyServer).where(ProxyServer.name == name)
            )).scalar_one_or_none()
            if not proxy:
                raise ValueError(f"Proxy '{name}' not found.")

            if custom_description is not _UNSET:
                proxy.custom_description = custom_description
            if workspace_label is not _UNSET:
                proxy.workspace_label = workspace_label
            await session.commit()
            await session.refresh(proxy)
            updated_desc = proxy.custom_description
            updated_label = proxy.workspace_label
            proxy_url = proxy.url
            proxy_transport = proxy.transport
            proxy_auth_mode = proxy.auth_mode

        await self._persist_proxy_content_hash(
            name, proxy_url, proxy_transport, proxy_auth_mode, updated_desc, updated_label
        )

        provider = self._active_handles.get(name)
        if provider:
            try:
                tools = await provider.server.list_tools()
                # Prefer DAL over raw SQL so embedding cleanup stays with route_store.
                from db_layer.route_store import delete_plugin_route_embeddings
                await delete_plugin_route_embeddings(f"proxy_{name}")
                self._remove_proxy_routes(name)
                from core.plugin_loader.plugin_registry import get_registry
                get_registry().events.emit(
                    "proxy.tools_discovered",
                    {
                        "name": name,
                        "tools": tools,
                        "custom_description": updated_desc,
                        "workspace_label": updated_label,
                    },
                )
                from core.context import route_registry
                from core_graph.worker.job_producer import enqueue_pending
                await enqueue_pending(route_registry)
            except Exception as e:
                logger.exception("Failed to re-embed routes for proxy '%s' after description/workspace_label update", name)
                return {
                    "name": name,
                    "customDescription": updated_desc,
                    "workspaceLabel": updated_label,
                    "status": "partial_error",
                    "error": "Failed to update proxy embeddings.",
                }

        return {
            "name": name,
            "customDescription": updated_desc,
            "workspaceLabel": updated_label,
            "status": "ok",
        }


proxy_manager = ProxyManager()
