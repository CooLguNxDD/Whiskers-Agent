"""
Registry managing authentication and credentials for registered plugins.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Any, Dict, Set, TYPE_CHECKING, Awaitable
from utils.server_config import PLUGIN_DIRECT_AUTH_TOKEN_DEFAULT_TTL_SECONDS
from core.proxy.ssrf_safety import _safe_async_client, _is_safe_url
from .auth_status import AuthStatus

if TYPE_CHECKING:
    from .plugin_registry import PluginRegistry

logger = logging.getLogger("whiskers.plugins")


def is_auth_error(headers: Any) -> bool:
    """True when a ``get_auth_headers()`` result is the degraded-failure shape, not real headers.

    ``get_auth_headers`` never raises — a circular delegation, a missing provider, or a provider's
    own ``{"error": ...}`` response all degrade to this shape instead of crashing the request. Any
    call site that forwards the result straight into a real HTTP request's ``headers=`` kwarg
    **must** check this first, or it sends this dict as literal (bogus) request headers instead of
    failing cleanly before dispatch.
    """
    return isinstance(headers, dict) and (headers.get("status") == "error" or "error" in headers)


class PluginAuthRegistry:
    """Registry subclass responsible for managing plugin authentication and tokens."""
    def __init__(self, registry: "PluginRegistry"):
        """Initialize the auth registry and subscribe to the plugin.boot event."""
        self._registry = registry
        self._auth_providers: Dict[str, Callable[[], Awaitable[dict[str, str]]]] = {}
        self._auth_delegates_reg: Dict[str, str] = {}
        self._needs_reauth: Set[str] = set()
        self._http = _safe_async_client(timeout=10)
        self._registry.events.on("plugin.boot", self._on_plugin_boot)

    async def _on_plugin_boot(self, plugin_id: str) -> None:
        """Clear stale direct-auth tokens before on_ready."""
        await self._vault_clear_direct_token(plugin_id)

    @property
    def _vault(self):
        return self._registry._vault

    @property
    def _relay(self):
        return self._registry._relay

    async def _vault_read_direct_token(self, plugin_id: str) -> str | None:
        """Read direct_auth_token and direct_auth_token_expires_at from Vault."""
        if self._vault is None:
            return None
        token = await self._vault.get(plugin_id, "direct_auth_token")
        expires_str = await self._vault.get(plugin_id, "direct_auth_token_expires_at")
        if not token:
            return None
        if expires_str:
            try:
                expires_at = datetime.fromisoformat(expires_str)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                now_utc = datetime.now(timezone.utc)
                if expires_at < now_utc + timedelta(seconds=60):
                    logger.info("PluginAuthRegistry: Token for '%s' is expired or close to expiry, forcing fresh login", plugin_id)
                    return None
            except Exception as exc:
                logger.warning("PluginAuthRegistry: Failed to parse token expiry for '%s': %s", plugin_id, exc)
                return None
        return token

    async def _vault_write_direct_token(self, plugin_id: str, token: str, expires_at: datetime) -> None:
        """Write direct_auth_token and direct_auth_token_expires_at to Vault."""
        if self._vault is not None:
            await self._vault.set(plugin_id, "direct_auth_token", token)
            await self._vault.set(plugin_id, "direct_auth_token_expires_at", expires_at.isoformat())

    async def _vault_clear_direct_token(self, plugin_id: str) -> None:
        """Clear direct_auth_token and direct_auth_token_expires_at from Vault."""
        if self._vault is not None:
            await self._vault.delete(plugin_id, "direct_auth_token")
            await self._vault.delete(plugin_id, "direct_auth_token_expires_at")

    def register_auth(self, plugin_id: str, fn: Callable[[], Awaitable[dict[str, str]]]) -> None:
        """Register the auth header provider function for this plugin."""
        self._auth_providers[plugin_id] = fn
        logger.info("PluginAuthRegistry: auth registered for '%s'", plugin_id)

    def set_auth_delegate(self, plugin_id: str, parent_plugin_id: str) -> None:
        """Declare that this plugin resolves auth via a parent plugin."""
        self._auth_delegates_reg[plugin_id] = parent_plugin_id
        logger.info("PluginAuthRegistry: '%s' auth delegates to '%s'", plugin_id, parent_plugin_id)

    def register_direct_auth(self, plugin_id: str, config: dict) -> None:
        """Register a standard direct-login auth config for plugin_id."""
        async def _fn() -> dict[str, str]:
            return await self._resolve_direct_auth(plugin_id, config)
        self.register_auth(plugin_id, _fn)
        logger.info("PluginAuthRegistry: direct auth config registered for '%s'", plugin_id)

    async def _resolve_direct_auth(self, plugin_id: str, config: dict) -> dict[str, str]:
        """Full auth resolution pipeline for a direct-login plugin."""
        base_headers: dict = config.get("base_headers", {})
        auth_header: str = config.get("auth_header", "authentication")
        provider: str = config.get("provider", "")
        layer2_oauth_enabled: bool = config.get("layer2_oauth_enabled", True)

        # 1. Layer 2 OAuth (PKCE flow already completed)
        if layer2_oauth_enabled and self._relay is not None:
            try:
                token = await self._relay.get_token(plugin_id, provider)
                if token:
                    return {**base_headers, "Authorization": f"Bearer {token}"}
            except Exception as exc:
                logger.warning(
                    "PluginAuthRegistry: Layer 2 token fetch failed for '%s': %s", plugin_id, exc
                )

        # 2. Vault-backed API Token
        if self._vault is not None:
            api_token = await self._vault.get(plugin_id, "api_token")
            if api_token:
                return {**base_headers, auth_header: api_token}

        # 3. Vault-backed direct token (survives reconnects + restarts)
        token = await self._vault_read_direct_token(plugin_id)
        if token:
            return {**base_headers, auth_header: token}

        # 3. Fresh direct login
        token = await self._direct_login_and_persist(plugin_id, config)
        if token:
            return {**base_headers, auth_header: token}

        logger.error("PluginAuthRegistry: No auth method succeeded for '%s'", plugin_id)
        self.mark_needs_reauth(plugin_id)
        return dict(base_headers)

    async def _direct_login_and_persist(self, plugin_id: str, config: dict) -> str | None:
        """Perform direct login and persist token to Vault."""
        username = ""
        username_provider = config.get("username_provider")
        if username_provider:
            if callable(username_provider):
                res = username_provider()
                if asyncio.iscoroutine(res):
                    username = await res
                else:
                    username = res
            else:
                username = str(username_provider)
        else:
            username = config.get("username", "")

        password = ""
        password_provider = config.get("password_provider")
        if password_provider:
            if callable(password_provider):
                res = password_provider()
                if asyncio.iscoroutine(res):
                    password = await res
                else:
                    password = res
            else:
                password = str(password_provider)
        else:
            password = config.get("password", "")

        login_url: str = config.get("login_url", "")
        default_ttl: int = config.get(
            "default_ttl", PLUGIN_DIRECT_AUTH_TOKEN_DEFAULT_TTL_SECONDS
        )

        if username and password and login_url:
            if not await _is_safe_url(login_url):
                logger.warning(
                    "PluginAuthRegistry: Rejected unsafe login_url for '%s': %s",
                    plugin_id,
                    login_url,
                )
                return None
            try:
                resp = await self._http.post(
                    login_url,
                    json={"username": username, "password": password},
                )
                resp.raise_for_status()
                data = resp.json()
                token_obj = data.get("token")
                token = token_obj.get("value") if isinstance(token_obj, dict) else token_obj
                if not token:
                    token = data.get("access_token", "")
                if token:
                    expires_in = default_ttl
                    if isinstance(token_obj, dict):
                        expires_in = int(token_obj.get("expires_in", default_ttl))
                    elif isinstance(data.get("expires_in"), (int, float)):
                        expires_in = int(data["expires_in"])
                    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=expires_in)

                    await self._vault_write_direct_token(plugin_id, token, expires_at)
                    return token
            except Exception as exc:
                logger.warning(
                    "PluginAuthRegistry: Direct login failed for '%s': %s", plugin_id, exc
                )
        return None

    async def get_auth_headers(
        self,
        plugin_id: str,
        _visited: frozenset[str] | None = None,
    ) -> dict[str, str]:
        """Fetch auth headers for a plugin, resolving delegation if necessary."""
        if _visited is None:
            _visited = frozenset()
        if plugin_id in _visited:
            # A misconfigured plugin's delegation cycle must not crash the MCP request
            # it's discovered inside — degrade to no-auth and let the caller surface
            # the resulting 401/403 instead of an unhandled exception.
            logger.critical(
                "PluginAuthRegistry: circular auth delegation detected for '%s' (chain: %s)",
                plugin_id, sorted(_visited),
            )
            return {"status": "error", "error": "circular_auth_delegation"}
        _visited = _visited | {plugin_id}
        fn = self._auth_providers.get(plugin_id)
        if fn:
            headers = await fn()
            if headers is not None and not is_auth_error(headers):
                self.clear_needs_reauth(plugin_id)
            return headers
        delegate = self._auth_delegates_reg.get(plugin_id)
        if delegate:
            headers = await self.get_auth_headers(delegate, _visited)
            if headers is not None and not is_auth_error(headers):
                self.clear_needs_reauth(plugin_id)
            return headers
        logger.critical(
            "PluginAuthRegistry: no auth registered for plugin_id='%s'. "
            "Register via ctx.register_auth() in on_load(), or declare 'requires' in manifest.json.",
            plugin_id,
        )
        return {"status": "error", "error": "no_auth_registered"}

    async def refresh_auth_headers(self, plugin_id: str) -> dict[str, str]:
        """Clears direct-token session in Vault, then re-runs get_auth_headers()."""
        await self._vault_clear_direct_token(plugin_id)
        return await self.get_auth_headers(plugin_id)

    def mark_needs_reauth(self, plugin_id: str) -> None:
        """Marks a plugin as requiring re-authentication (e.g. invalid credentials)."""
        self._needs_reauth.add(plugin_id)
        logger.info("PluginAuthRegistry: '%s' marked as needs_reauth", plugin_id)

    def clear_needs_reauth(self, plugin_id: str) -> None:
        """Clears the needs_reauth flag for a plugin."""
        if plugin_id in self._needs_reauth:
            self._needs_reauth.discard(plugin_id)
            logger.info("PluginAuthRegistry: '%s' cleared needs_reauth", plugin_id)

    def get_auth_status(self, plugin_id: str) -> AuthStatus:
        """Returns the current authentication status for a plugin."""
        return AuthStatus.NEEDS_REAUTH if plugin_id in self._needs_reauth else AuthStatus.OK

    async def aclose(self) -> None:
        """Close the underlying HTTP client; idempotent and safe if never initialised."""
        http = getattr(self, "_http", None)
        if http is None:
            return
        self._http = None
        await http.aclose()
