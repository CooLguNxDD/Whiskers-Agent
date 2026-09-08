"""
ExternalOAuthRelay — Layer 2 generic outbound OAuth for plugins.

Handles PKCE authorization flows to external providers (e.g. Whiskers Agent backend,
GitHub, Slack) on behalf of a named plugin.  Encrypted tokens are persisted
to ``plugin_oauth_tokens``; transient PKCE state lives in
``plugin_oauth_pkce_state`` (with the ``context`` JSONB column from core_012).

Provider configuration is supplied by the plugin manifest's ``external_oauth``
block, resolved at plugin-load time by the plugin loader.

PKCE encoding
-------------
Default: RFC 7636 S256 — base64url(sha256(verifier)).
Whiskers Agent backend variant: ``"pkce": "S256-hex"`` in the manifest block, which
produces hex(sha256(verifier)).  The encoding is picked per-provider so the
relay stays generic.

Public-path registration
------------------------
Routes ``/oauth/plugin/{provider}/authorize`` and
``/oauth/plugin/{provider}/callback`` must be mounted **before** FastMCP's
auth middleware.  See ``oauth/oauth_routes.py`` for the Starlette mount.
"""

import asyncio
import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import Text, cast, delete, func, select
from sqlalchemy.dialects.postgresql import insert

from core.proxy import ssrf_safety
from db_layer.connection import get_async_session
from db_layer.models import PluginOAuthPKCEState, PluginOAuthToken
from db_layer.plugin_registry_store import DBPluginRegistry
from db_layer.vault import VaultService
from utils.server_config import PLUGIN_EXTERNAL_OAUTH_TOKEN_DEFAULT_TTL_SECONDS

logger = logging.getLogger("whiskers")

_REFRESH_LOCK_MAX = 1000
_REFRESH_LOCK_TTL = 3600  # seconds; evict idle (p,prov) locks after 1h

# Refresh buffer: refresh if <60 s remain on the access token
_REFRESH_BUFFER = 60
_PLUGIN_TOKEN_DEFAULT_TTL = PLUGIN_EXTERNAL_OAUTH_TOKEN_DEFAULT_TTL_SECONDS


def _pkce_s256(verifier: str, encoding: str = "S256") -> str:
    """Compute PKCE S256 challenge (RFC or Whiskers Agent hex variant)."""
    import base64
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    if encoding == "S256-hex":
        return digest.hex()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _to_utc_aware(dt: datetime) -> datetime:
    """Ensure a datetime is UTC-aware, adjusting if already aware or assuming UTC if naive."""
    if dt.tzinfo:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _encrypted_text(value: str):
    return func.pgp_sym_encrypt(value, func.current_setting("app.master_key"))


class ExternalOAuthRelay:
    """Generic OAuth 2.0 + PKCE relay for external provider tokens per plugin."""

    def __init__(
        self,
        vault: VaultService,
        plugin_manifests: dict[str, dict],
        *,
        session_factory=None,  # reserved; uses module-level get_async_session
    ) -> None:
        self._vault = vault
        # plugin_manifests: {plugin_id: resolved manifest dict}
        self._manifests = plugin_manifests
        # Per-(plugin_id, provider) locks to prevent concurrent refresh races
        self._refresh_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._refresh_lock_ts: dict[tuple[str, str], float] = {}
        # Persistent HTTP client — reused across all token exchange calls
        self._http = ssrf_safety._safe_async_client(timeout=30)
        # Auth delegation map: dependent_plugin_id -> parent_plugin_id
        self._auth_delegates: dict[str, str] = {}

    #  build authorize URL

    async def build_authorize_url(
        self,
        plugin_id: str,
        provider: str,
        redirect_uri: str,
        extra_context: dict | None = None,
    ) -> str:
        """Generate the provider authorize URL and stash the PKCE state in the DB."""
        await self._ensure_plugin_registered(plugin_id)
        cfg = self._provider_config(plugin_id, provider)
        authorize_url = cfg["authorize_url"]
        client_id = cfg["client_id"]
        scopes = cfg.get("scopes", [])
        pkce_encoding = cfg.get("pkce", "S256")

        verifier = secrets.token_urlsafe(64)[:96]
        challenge = _pkce_s256(verifier, encoding=pkce_encoding)
        state = secrets.token_urlsafe(32)

        # Persist PKCE state (encrypted verifier)
        async with get_async_session() as session:
            stmt = insert(PluginOAuthPKCEState).values(
                state=state,
                plugin_id=plugin_id,
                provider=provider,
                code_verifier=_encrypted_text(verifier),
                redirect_uri=redirect_uri,
                expires_at=_now() + timedelta(minutes=10),
                context=extra_context or {},
            )
            await session.execute(stmt)
            await session.commit()

        # Build the provider authorization URL
        scope_str = " ".join(scopes)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope_str,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",  # providers see standard method
        }
        query = urlencode(params)
        full_url = f"{authorize_url}{'&' if '?' in authorize_url else '?'}{query}"
        logger.info(
            "ExternalOAuthRelay: built authorize URL for plugin=%s provider=%s",
            plugin_id, provider,
        )
        return full_url

    # handle callback

    async def handle_callback(
        self,
        code: str,
        state: str,
    ) -> dict:
        """Exchange the authorization code and persist encrypted tokens.

        Returns ``{"plugin_id": ..., "provider": ..., "context": {...}}`` so the
        caller does not need a separate pre-query against ``plugin_oauth_pkce_state``.
        """
        # Consume PKCE state atomically — DELETE...RETURNING prevents two concurrent
        # callbacks from both validating the same state and racing the auth code.
        async with get_async_session() as session:
            stmt = (
                delete(PluginOAuthPKCEState)
                .where(PluginOAuthPKCEState.state == state)
                .returning(
                    PluginOAuthPKCEState.plugin_id,
                    PluginOAuthPKCEState.provider,
                    func.coalesce(
                        cast(
                            func.pgp_sym_decrypt(
                                PluginOAuthPKCEState.code_verifier,
                                func.current_setting("app.master_key"),
                            ),
                            Text,
                        ),
                        "",
                    ).label("code_verifier"),
                    PluginOAuthPKCEState.redirect_uri,
                    PluginOAuthPKCEState.expires_at,
                    PluginOAuthPKCEState.context,
                )
            )
            row = await session.execute(stmt)
            rec = row.first()
            await session.commit()

        if rec is None:
            raise ValueError(f"Unknown or expired PKCE state: {state}")

        db_plugin_id, db_provider, verifier, redirect_uri, expires_at, context_json = rec

        expires_at_aware = _to_utc_aware(expires_at)
        if expires_at_aware < _now():
            raise ValueError("PKCE state has expired")

        plugin_id = db_plugin_id
        provider = db_provider

        cfg = self._provider_config(plugin_id, provider)
        token_url = cfg["token_url"]
        client_id = cfg["client_id"]
        pkce_encoding = cfg.get("pkce", "S256")

        # Client secret from vault
        secret_key = f"{provider.upper()}_CLIENT_SECRET"
        client_secret = await self._vault.get(plugin_id, secret_key)

        # Exchange code at provider
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        }
        if client_secret:
            payload["client_secret"] = client_secret

        resp = await self._http.post(token_url, data=payload)
        resp.raise_for_status()
        token_data = resp.json()

        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        expires_in = token_data.get("expires_in", _PLUGIN_TOKEN_DEFAULT_TTL)
        scopes = token_data.get("scope", "").split()
        expires_at_new = _now() + timedelta(seconds=int(expires_in))

        await self._store_tokens(
            plugin_id, provider, access_token, refresh_token,
            scopes, expires_at_new,
        )

        logger.info(
            "ExternalOAuthRelay: stored tokens for plugin=%s provider=%s",
            plugin_id, provider,
        )

        return {
            "plugin_id": plugin_id,
            "provider": provider,
            "context": context_json or {},
        }

    # get token
    async def get_token(
        self,
        plugin_id: str,
        provider: str,
        *,
        force_refresh: bool = False,
        _visited: frozenset[str] | None = None,
    ) -> str:
        """Return a valid access token, auto-refreshing if < 60 s remain."""

        # DRY: Helper to check expiration
        def _is_expired(expiry) -> bool:
            if not expiry:
                return False
            expires_at_aware = _to_utc_aware(expiry)
            return expires_at_aware < _now() + timedelta(seconds=_REFRESH_BUFFER)

        if _visited is None:
            _visited = frozenset()
        if plugin_id in _visited:
            raise RuntimeError(f"Circular auth delegation detected involving plugin='{plugin_id}'")
        _visited = _visited | {plugin_id}

        rec = await self._load_token_record(plugin_id, provider)

        if rec is None:
            # Try delegating to a registered parent plugin
            delegate = self._auth_delegates.get(plugin_id)
            if delegate:
                logger.debug(
                    "ExternalOAuthRelay: '%s' delegating get_token to '%s'",
                    plugin_id,
                    delegate,
                )
                return await self.get_token(delegate, provider, force_refresh=force_refresh, _visited=_visited)
            raise RuntimeError(
                f"No token found for plugin={plugin_id} provider={provider}. "
                "Complete the OAuth flow first."
            )

        access_token, refresh_token, expires_at = rec

        # Fast path: Token is valid and no force refresh requested
        if not force_refresh and not _is_expired(expires_at):
            return access_token

        if not refresh_token:
            raise RuntimeError(
                f"Token expired and no refresh token available for "
                f"plugin={plugin_id} provider={provider}."
            )

        # Cleaner lock initialization
        lock_key = (plugin_id, provider)
        if lock_key not in self._refresh_locks:
            self._sweep_refresh_locks()
            if len(self._refresh_locks) >= _REFRESH_LOCK_MAX:
                stale = [k for k in self._refresh_locks if k[0] not in self._manifests]
                for k in stale:
                    del self._refresh_locks[k]
                    self._refresh_lock_ts.pop(k, None)
            self._refresh_locks[lock_key] = asyncio.Lock()
        self._refresh_lock_ts[lock_key] = time.monotonic()
        lock = self._refresh_locks[lock_key]

        async with lock:
            # Re-read inside the lock
            locked_rec = await self._load_token_record(plugin_id, provider)
            if locked_rec is None:
                raise RuntimeError(
                    f"No token found for plugin={plugin_id} provider={provider} "
                    "during refresh."
                )

            new_access_token, new_refresh_token, new_expires_at = locked_rec

            # If the token changed while we waited for the lock, another task 
            # already handled the refresh. We can skip doing it again.
            if new_access_token != access_token:
                if not _is_expired(new_expires_at):
                    return new_access_token

            # Standard double-check (if force_refresh wasn't the trigger)
            if not force_refresh and not _is_expired(new_expires_at):
                return new_access_token

            if not new_refresh_token:
                raise RuntimeError(
                    f"Token expired and no refresh token available for "
                    f"plugin={plugin_id} provider={provider}."
                )

            return await self._refresh(plugin_id, provider, new_refresh_token)

    async def has_token(self, plugin_id: str, provider: str) -> bool:
        """Return True if a valid (or refreshable) token exists for the plugin/provider pair."""
        try:
            token = await self.get_token(plugin_id, provider, force_refresh=False)
            return bool(token)
        except Exception:
            return False

    async def revoke_token(self, plugin_id: str, provider: str) -> None:
        """Delete the stored OAuth token for the given plugin/provider pair."""
        async with get_async_session() as session:
            await session.execute(
                delete(PluginOAuthToken).where(
                    PluginOAuthToken.plugin_id == plugin_id,
                    PluginOAuthToken.provider == provider,
                )
            )
            await session.commit()

    # refresh
    async def _refresh(
        self, plugin_id: str, provider: str, refresh_token: str
    ) -> str:
        """Exchange a refresh token, persist updated tokens, return new access token."""
        cfg = self._provider_config(plugin_id, provider)
        token_url = cfg["token_url"]
        client_id = cfg["client_id"]

        secret_key = f"{provider.upper()}_CLIENT_SECRET"
        client_secret = await self._vault.get(plugin_id, secret_key)

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if client_secret:
            payload["client_secret"] = client_secret

        resp = await self._http.post(token_url, data=payload)
        resp.raise_for_status()
        token_data = resp.json()

        new_access = token_data.get("access_token", "")
        new_refresh = token_data.get("refresh_token", refresh_token)
        expires_in = token_data.get("expires_in", _PLUGIN_TOKEN_DEFAULT_TTL)
        scopes = token_data.get("scope", "").split()
        expires_at = _now() + timedelta(seconds=int(expires_in))

        await self._store_tokens(
            plugin_id, provider, new_access, new_refresh, scopes, expires_at
        )
        logger.info(
            "ExternalOAuthRelay: refreshed token for plugin=%s provider=%s",
            plugin_id, provider,
        )
        return new_access

    # store tokens

    async def _store_tokens(
        self,
        plugin_id: str,
        provider: str,
        access_token: str,
        refresh_token: str,
        scopes: list[str],
        expires_at: datetime,
    ) -> None:
        await self._ensure_plugin_registered(plugin_id)
        async with get_async_session() as session:
            stmt = insert(PluginOAuthToken).values(
                plugin_id=plugin_id,
                provider=provider,
                access_token=_encrypted_text(access_token),
                refresh_token=_encrypted_text(refresh_token),
                scopes=" ".join(scopes),
                expires_at=expires_at,
                created_at=func.now(),
                updated_at=func.now(),
            ).on_conflict_do_update(
                index_elements=["plugin_id", "provider"],
                set_={
                    "access_token": _encrypted_text(access_token),
                    "refresh_token": _encrypted_text(refresh_token),
                    "scopes": " ".join(scopes),
                    "expires_at": expires_at,
                    "updated_at": func.now(),
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def _load_token_record(
        self,
        plugin_id: str,
        provider: str,
    ) -> tuple[str, str, datetime | None] | None:
        """Fetch the decrypted access/refresh tokens and expiry for a provider."""
        async with get_async_session() as session:
            stmt = select(
                func.coalesce(
                    cast(
                        func.pgp_sym_decrypt(
                            PluginOAuthToken.access_token,
                            func.current_setting("app.master_key"),
                        ),
                        Text,
                    ),
                    "",
                ).label("access_token"),
                func.coalesce(
                    cast(
                        func.pgp_sym_decrypt(
                            PluginOAuthToken.refresh_token,
                            func.current_setting("app.master_key"),
                        ),
                        Text,
                    ),
                    "",
                ).label("refresh_token"),
                PluginOAuthToken.expires_at,
            ).where(
                PluginOAuthToken.plugin_id == plugin_id,
                PluginOAuthToken.provider == provider,
            )
            row = await session.execute(stmt)
            return row.first()

    # helpers

    async def _ensure_plugin_registered(self, plugin_id: str) -> None:
        """Ensure the plugin exists in ``plugins`` before FK-backed writes.

        The normal path registers plugin manifests at startup, but OAuth flows
        can still arrive before that DB upsert happens or after a partial
        startup. In that case, self-heal by upserting from the cached manifest.
        """
        manifest = self._manifests.get(plugin_id)
        if not manifest:
            raise ValueError(
                f"Plugin '{plugin_id}' is not known to the OAuth relay. "
                "Ensure the plugin manifest is loaded before starting the OAuth flow."
            )

        registry = DBPluginRegistry()
        existing = await registry.get(plugin_id)
        if existing is not None:
            return

        await registry.register(manifest)
        logger.info(
            "ExternalOAuthRelay: auto-registered missing plugin row for plugin=%s",
            plugin_id,
        )

    def _provider_config(self, plugin_id: str, provider: str) -> dict[str, Any]:
        """Return the resolved provider config from the plugin manifest."""
        manifest = self._manifests.get(plugin_id, {})
        external_oauth = manifest.get("external_oauth", {})
        cfg = external_oauth.get(provider)
        if not cfg:
            raise ValueError(
                f"Plugin '{plugin_id}' has no external_oauth config for provider '{provider}'. "
                f"Add an 'external_oauth.{provider}' block to its manifest.json."
            )

        # Validate required URL fields are non-empty (catch missing env vars early)
        for field in ("authorize_url", "token_url"):
            if not cfg.get(field, "").strip():
                raise ValueError(
                    f"Plugin '{plugin_id}' external_oauth.{provider}.{field} is empty. "
                    f"Ensure the referenced environment variable is set."
                )
        return cfg

    def update_manifests(self, plugin_manifests: dict[str, dict]) -> None:
        """Replace the cached manifests dict (called after plugin reload)."""
        # Preserve any previously registered auth delegates — delegates are
        # managed by the plugin loader and should survive manifest reloads.
        self._manifests = plugin_manifests

    async def upsert_manifest(self, plugin_id: str, manifest: dict) -> None:
        """Merge a synthetic or plugin manifest into the cached manifests.
        
        Does not clobber other plugin manifests.
        """
        urls_to_validate: list[str] = []
        for key in ("token_url", "authorization_url", "authorize_url"):
            val = manifest.get(key)
            if val and isinstance(val, str):
                urls_to_validate.append(val)

        ext_oauth = manifest.get("external_oauth")
        if isinstance(ext_oauth, dict):
            for prov_cfg in ext_oauth.values():
                if isinstance(prov_cfg, dict):
                    for key in ("token_url", "authorization_url", "authorize_url"):
                        val = prov_cfg.get(key)
                        if val and isinstance(val, str):
                            urls_to_validate.append(val)

        for url in urls_to_validate:
            if not await ssrf_safety._is_safe_url(url):
                raise ValueError(f"ExternalOAuthRelay: Unsafe URL '{url}' in manifest for plugin '{plugin_id}'")

        self._manifests[plugin_id] = manifest
        logger.info("ExternalOAuthRelay: upserted manifest for %s", plugin_id)

    def register_delegate(self, plugin_id: str, parent_plugin_id: str) -> None:
        """Register plugin_id to inherit tokens from parent_plugin_id."""
        self._auth_delegates[plugin_id] = parent_plugin_id
        logger.info(
            "ExternalOAuthRelay: '%s' delegates auth to '%s'",
            plugin_id,
            parent_plugin_id,
        )
    
    async def store_direct_token(
        self, plugin_id: str, token: str, expires_at: datetime
    ) -> None:
        """DEPRECATED: Persist a direct-login token."""
        logger.warning("ExternalOAuthRelay.store_direct_token is deprecated. Direct login tokens are now stored in Vault.")
        return None

    async def get_direct_token(self, plugin_id: str) -> str | None:
        """DEPRECATED: Return a valid direct-login token from DB."""
        logger.warning("ExternalOAuthRelay.get_direct_token is deprecated. Direct login tokens are now stored in Vault.")
        return None

    async def discard_direct_tokens(self, plugin_id: str) -> None:
        """DEPRECATED: Delete the direct-login token row."""
        logger.warning("ExternalOAuthRelay.discard_direct_tokens is deprecated. Direct login tokens are now stored in Vault.")
        return None

    def _sweep_refresh_locks(self, ttl: float = _REFRESH_LOCK_TTL) -> int:
        """Evict (plugin,provider) refresh locks idle longer than ttl. Never evicts a held lock. Returns count removed."""
        cutoff = time.monotonic() - ttl
        stale = [
            k for k, ts in self._refresh_lock_ts.items()
            if ts < cutoff and not (k in self._refresh_locks and self._refresh_locks[k].locked())
        ]
        for k in stale:
            self._refresh_locks.pop(k, None)
            self._refresh_lock_ts.pop(k, None)
        return len(stale)

    def revoke_refresh_lock(self, plugin_id: str, provider: str) -> None:
        """Remove the per-(plugin_id, provider) refresh lock."""
        self._refresh_locks.pop((plugin_id, provider), None)
        self._refresh_lock_ts.pop((plugin_id, provider), None)

    async def aclose(self) -> None:
        """Close the underlying HTTP client; idempotent and safe if never initialised."""
        http = getattr(self, "_http", None)
        if http is None:
            return
        self._http = None
        await http.aclose()
