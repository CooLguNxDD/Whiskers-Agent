"""
Whiskers Agent OAuth 2.0 Authorization Server Provider.

.. deprecated::
    This module is superseded by ``oauth.oauth_service.OAuthService`` +
    ``oauth.oauth_service.OAuthService_FastMCPProvider`` (Layer 1) and
    ``oauth.oauth_relay.ExternalOAuthRelay`` (Layer 2).

    It is kept for one PR cycle so that callers importing
    ``LegacyOAuthProvider`` continue to work while the migration completes.
    It will be deleted in a follow-up PR once ``core/context.py`` fully
    relies on the DB-backed services.

    Any import of this module will emit a ``DeprecationWarning``.

Delegates authentication to the Whiskers Agent backend's OAuth 2.0 Authorization Code
+ PKCE flow (backend MR #5165) via the **Whiskers Agent client** login page.

Flow
----
1. MCP client hits ``/authorize`` → redirected to the Whiskers Agent **client**
   at ``{WHISKERS_CLIENT_URL}/oauth-login?response_type=code&...``.
2. User logs in on the Whiskers Agent client.  The Angular app calls the backend
   ``POST /api/v1/login/oauth`` and handles MFA if required.
3. Backend returns ``{redirect: "<callback>?code=BACKEND_CODE&state=STATE"}``
   and the client follows it, landing on our ``/whiskers-auth/callback``.
4. We create our own MCP auth code mapped to the backend code.
5. MCP client exchanges code at ``/token`` → we forward to backend
   ``POST /api/v1/oauth/token`` with the PKCE ``code_verifier``.
6. Backend validates PKCE & returns ``{access_token, token_type, expires_in}``.
"""

import asyncio
import warnings
warnings.warn(
    "oauth.oauth_provider (LegacyOAuthProvider) is deprecated and will be removed in a "
    "follow-up PR.  Use oauth.oauth_service.OAuthService_FastMCPProvider instead.",
    DeprecationWarning,
    stacklevel=2,
)

import hashlib
import json
import logging
import os
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests
from fastmcp.server.auth import OAuthProvider
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from pydantic import AnyUrl
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from core.context import run_if_db_available

_CLIENTS_FILE = Path(__file__).resolve().parent.parent / ".mcp_clients.json"
_PENDING_AUTH_TTL = 600

logger = logging.getLogger("whiskers")

def _generate_pkce() -> tuple[str, str]:
    """Generate a PKCE code_verifier (43-128 chars) and S256 code_challenge.

    The Whiskers Agent backend uses ``hex(sha256(verifier))`` for challenge verification.
    """
    code_verifier = secrets.token_urlsafe(64)[:96]
    code_challenge = hashlib.sha256(code_verifier.encode("ascii")).hexdigest()
    return code_verifier, code_challenge


class LegacyOAuthProvider(OAuthProvider):
    """OAuth 2.0 provider that delegates to the Whiskers Agent backend."""

    def __init__(
        self,
        api_url: str,
        client_url: str,
        backend_client_id: str,
        backend_redirect_uri: str,
        base_url: str = "http://localhost:10000",
    ) -> None:
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["whiskers"],
            ),
        )
        self._api_url = api_url
        self._client_url = client_url.rstrip("/")
        self._backend_client_id = backend_client_id
        self._backend_redirect_uri = backend_redirect_uri

        # In-memory stores
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._load_clients()
        self._access_tokens: dict[str, AccessToken] = {}
        # Pending MCP authorize requests keyed by random auth_state
        self._pending_auths: dict[str, dict[str, Any]] = {}
        # Our MCP auth codes
        self._auth_codes: dict[str, AuthorizationCode] = {}
        # our_code → {backend_code, code_verifier, backend_redirect_uri}
        self._code_to_backend: dict[str, dict[str, str]] = {}
        # auth_state → {code_verifier, code_challenge} for pending logins
        self._pending_pkce: dict[str, dict[str, str]] = {}

    # -- Client persistence ----------------------------------------------------

    def _load_clients(self) -> None:
        if _CLIENTS_FILE.exists():
            try:
                data = json.loads(_CLIENTS_FILE.read_text(encoding="utf-8"))
                for cid, raw in data.items():
                    self._clients[cid] = OAuthClientInformationFull.model_validate(raw)
                logger.info(f"Loaded {len(self._clients)} registered client(s) from {_CLIENTS_FILE.name}")
            except Exception as exc:
                logger.warning(f"Failed to load clients file: {exc}")

    def _save_clients(self) -> None:
        try:
            data = {cid: c.model_dump(mode="json") for cid, c in self._clients.items()}
            _CLIENTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Failed to save clients file: {exc}")

    # -- Client registration ---------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        client = self._clients.get(client_id)
        if client is None:
            client = await run_if_db_available("db_load_client", client_id)
            if client:
                self._clients[client_id] = client  # warm the memory cache
        return client

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info
        self._save_clients()
        await run_if_db_available("db_save_client", client_info)
        logger.info(f"Registered OAuth client: {client_info.client_id}")

    async def auto_register_client(self, client_id: str, redirect_uri_str: str) -> None:
        """Auto-register (or update) a client that skips POST /register and goes
        directly to GET /authorize with a pre-configured client_id.

        Some MCP clients (e.g. Claude Desktop, MCP Inspector) never call the
        /register endpoint. They use a fixed UUID as their client_id and pass a
        (potentially ephemeral) redirect_uri in the /authorize query string.
        We intercept the request in middleware before FastMCP validates the
        client, register the client on the fly, and let the flow continue.

        This is safe because PKCE is still enforced — we are only relaxing the
        requirement that clients pre-register via the /register endpoint.
        """
        try:
            parsed_uri = AnyUrl(redirect_uri_str)
        except Exception:
            logger.warning(f"Auto-registration skipped: invalid redirect_uri {redirect_uri_str!r}")
            return

        existing = self._clients.get(client_id)
        if existing and existing.redirect_uris and parsed_uri in existing.redirect_uris:
            return  # Already registered with this exact redirect_uri

        client = OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=[parsed_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope="whiskers",
        )
        await self.register_client(client)
        if existing:
            logger.info(f"Updated OAuth client {client_id!r} redirect_uri → {redirect_uri_str}")
        else:
            logger.info(f"Auto-registered OAuth client {client_id!r} with redirect_uri={redirect_uri_str}")

    def _sweep_pending(self) -> None:
        """Remove expired pending authorization and PKCE entries.

        Removes entries where time.time() - entry["_created_at"] > _PENDING_AUTH_TTL.
        """
        now = time.time()

        expired_auth_keys = [
            key for key, entry in self._pending_auths.items()
            if now - entry.get("_created_at", 0) > _PENDING_AUTH_TTL
        ]
        for key in expired_auth_keys:
            self._pending_auths.pop(key, None)

        expired_pkce_keys = [
            key for key, entry in self._pending_pkce.items()
            if now - entry.get("_created_at", 0) > _PENDING_AUTH_TTL
        ]
        for key in expired_pkce_keys:
            self._pending_pkce.pop(key, None)

    # -- Authorization ---------------------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Redirect the user to the Whiskers Agent client's OAuth login page."""
        self._sweep_pending()
        auth_state = secrets.token_urlsafe(32)
        self._pending_auths[auth_state] = {
            "client_id": client.client_id,
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "scopes": params.scopes or [],
            "state": params.state,
            "resource": params.resource,
            "_created_at": time.time(),
        }

        # Pre-generate the PKCE pair for the backend communication
        code_verifier, code_challenge = _generate_pkce()
        self._pending_pkce[auth_state] = {
            "code_verifier": code_verifier,
            "code_challenge": code_challenge,
            "_created_at": time.time(),
        }

        # Build the Whiskers Agent client's /oauth-login URL with backend OAuth params.
        # The Angular app shows the portal login form, POSTs to the backend's
        # /login/oauth endpoint, and follows the backend's {redirect: "..."}
        # response back to our /whiskers-auth/callback.
        oauth_params = {
            "response_type": "code",
            "client_id": self._backend_client_id,
            "redirect_uri": self._backend_redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": auth_state,
            "scope": "ALL",
        }
        login_url = f"{self._client_url}/oauth-login?{urllib.parse.urlencode(oauth_params)}"
        logger.info(f"OAuth authorize → Whiskers Agent client")
        logger.info(f"  backend_client_id: {self._backend_client_id!r}")
        logger.info(f"  backend_redirect_uri: {self._backend_redirect_uri!r}")
        logger.info(f"  login_url: {login_url}")
        return login_url

    # -- Authorization code loading --------------------------------------------

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        return self._auth_codes.get(authorization_code)

    # -- Token exchange --------------------------------------------------------

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        code = authorization_code.code
        backend_info = self._code_to_backend.pop(code, None)
        if not backend_info:
            raise TokenError("invalid_grant", "Authorization code not found or already used")

        self._auth_codes.pop(code, None)

        # Exchange the backend authorization code for an access token
        token_payload = {
            "grant_type": "authorization_code",
            "code": backend_info["backend_code"],
            "redirect_uri": backend_info["backend_redirect_uri"],
            "code_verifier": backend_info["code_verifier"],
        }

        try:
            # Blocking requests call offloaded so a slow backend token endpoint
            # (up to the 30s timeout) can't stall the event loop for every
            # concurrent OAuth login (CLAUDE.md "Non-blocking" guardrail).
            resp = await asyncio.to_thread(
                requests.post,
                f"{self._api_url}/api/v1/oauth/token",
                json=token_payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            token_data = resp.json()
        except requests.exceptions.HTTPError as e:
            body = ""
            try:
                body = e.response.text
            except Exception:
                logger.debug("oauth_provider.py: swallowed exception", exc_info=True)
            logger.error(f"Backend token exchange failed: {e.response.status_code} {body}")
            raise TokenError("invalid_grant", f"Backend token exchange failed: {body}")
        except Exception as exc:
            logger.error(f"Backend token exchange error: {exc}", exc_info=True)
            raise TokenError("invalid_grant", f"Backend token exchange error: {exc}")

        access_token_str = token_data.get("access_token", "")
        expires_in = token_data.get("expires_in", 3600)

        access = AccessToken(
            token=access_token_str,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + expires_in,
        )
        self._access_tokens[access_token_str] = access
        await run_if_db_available("db_save_token", access)

        logger.info("OAuth token exchange successful (via backend)")
        return OAuthToken(
            access_token=access_token_str,
            token_type=token_data.get("token_type", "Bearer"),
            expires_in=expires_in,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    # -- Refresh token (not supported – backend tokens are long-lived) ---------

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        from mcp.server.auth.errors import TokenError
        raise TokenError("invalid_grant", "Refresh tokens not supported – re-authorize")

    # -- Token loading & revocation --------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        at = self._access_tokens.get(token)
        if at is None:
            at = await run_if_db_available("db_load_token", token)
            if at:
                self._access_tokens[token] = at  # warm the memory cache
        if at and at.expires_at and at.expires_at < int(time.time()):
            self._access_tokens.pop(token, None)
            await run_if_db_available("db_delete_token", token)
            return None
        return at

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
            await run_if_db_available("db_delete_token", token.token)
        logger.info("OAuth token revoked")

    # -- Helpers for the callback route handler ---------------------------------

    def handle_backend_callback(
        self,
        auth_state: str,
        backend_code: str,
    ) -> tuple[str, str | None]:
        """
        Handle the callback from the Whiskers Agent client after a successful login.

        The Whiskers Agent client follows the backend's ``{redirect: "..."}`` response
        which lands on our ``/whiskers-auth/callback?code=CODE&state=STATE``.

        Returns ``(mcp_redirect_url, error_or_none)``.
        """
        pending = self._pending_auths.pop(auth_state, None)
        pkce = self._pending_pkce.pop(auth_state, None)
        if not pending or not pkce:
            return ("", "Invalid or expired authorization state")

        # Create our own MCP auth code
        our_code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=our_code,
            client_id=pending["client_id"],
            redirect_uri=pending["redirect_uri"],
            redirect_uri_provided_explicitly=pending["redirect_uri_provided_explicitly"],
            code_challenge=pending["code_challenge"],
            scopes=pending["scopes"],
            expires_at=time.time() + 300,
            resource=pending.get("resource"),
        )
        self._auth_codes[our_code] = auth_code

        # Map our code → backend info for token exchange later
        self._code_to_backend[our_code] = {
            "backend_code": backend_code,
            "code_verifier": pkce["code_verifier"],
            "backend_redirect_uri": self._backend_redirect_uri,
        }

        # Redirect MCP client back to their redirect_uri
        redirect_uri = pending["redirect_uri"]
        params: dict[str, str] = {"code": our_code}
        if pending.get("state"):
            params["state"] = pending["state"]
        separator = "&" if "?" in redirect_uri else "?"
        redirect_url = f"{redirect_uri}{separator}{urllib.parse.urlencode(params)}"
        return (redirect_url, None)
