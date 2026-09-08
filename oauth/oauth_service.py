"""
OAuthService — Layer 1 inbound auth: RS256 JWT, JWKS endpoint, JTI revocation.

This service owns the full MCP OAuth 2.1 lifecycle:
  - Keypair generation and rotation (auth_keypairs table, pgcrypto-encrypted)
  - Client registration with encrypted client_secret
  - Authorization-code grant with PKCE S256 (RFC-compliant base64url encoding)
  - Client-credentials grant (server-to-server)
  - Refresh-token rotation
  - Token validation and JTI-based revocation list

Integration with FastMCP:
  Wrap this service in ``OAuthService_FastMCPProvider`` (at the bottom of this
  file) and pass it as ``auth=`` to the FastMCP constructor.  The adapter
  delegates all OAuthProvider abstract methods to the service.
"""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote as _urlquote

from cachetools import TTLCache

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend
from fastmcp.server.auth import OAuthProvider
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlalchemy import text

from db_layer.connection import get_async_session
from utils.server_config import (
    OAUTH_ACCESS_TOKEN_TTL_SECONDS,
    OAUTH_AUTH_CODE_TTL_SECONDS,
    OAUTH_REFRESH_TOKEN_TTL_SECONDS,
)

logger = logging.getLogger("whiskers")


class OcatAccessToken(AccessToken):
    """AccessToken + JWT claims (e.g. ocat_role) for role-based downstream checks.

    Upstream mcp.server.auth.provider.AccessToken has no `claims` field and
    ignores unknown kwargs (pydantic extra=ignore), so plain AccessToken(claims=...)
    silently drops the value.
    """
    claims: dict | None = None


# ---------------------------------------------------------------------------
# Token lifetimes
# ---------------------------------------------------------------------------
ACCESS_TOKEN_TTL = OAUTH_ACCESS_TOKEN_TTL_SECONDS
REFRESH_TOKEN_TTL = OAUTH_REFRESH_TOKEN_TTL_SECONDS
AUTH_CODE_TTL = OAUTH_AUTH_CODE_TTL_SECONDS


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OAuthError(Exception):
    """Base class for OAuth errors."""
    error_code: str = "oauth_error"

class InvalidClientError(OAuthError):
    """
    Exception raised for an invalid client.
    """
    error_code = "invalid_client"

class InvalidGrantError(OAuthError):
    """
    Exception raised for an invalid grant.
    """
    error_code = "invalid_grant"

class UnauthorizedGrantError(OAuthError):
    """
    Exception raised for an unauthorized grant.
    """
    error_code = "unauthorized_client"

class InvalidTokenError(OAuthError):
    """
    Exception raised for an invalid token.
    """
    error_code = "invalid_token"

class ExpiredTokenError(InvalidTokenError):
    """
    Exception raised for an expired token.
    """
    error_code = "token_expired"

class RevokedTokenError(InvalidTokenError):
    """
    Exception raised for a revoked token.
    """
    error_code = "token_revoked"

class KeypairNotFoundError(InvalidTokenError):
    """
    Exception raised when a keypair is not found.
    """
    error_code = "keypair_not_found"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _pkce_s256(verifier: str, encoding: str = "S256") -> str:
    """Compute PKCE S256 challenge.

    ``encoding`` values:
      - ``"S256"``      — RFC 7636 base64url(sha256(verifier)) [default]
      - ``"S256-hex"``  — Whiskers Agent backend variant: hex(sha256(verifier))
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    if encoding == "S256-hex":
        return digest.hex()
    # RFC 7636: base64url without padding
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _kid_for_keypair(public_key_pem: str) -> str:
    """Derive a stable kid from the public key bytes (SHA-256 thumbprint prefix)."""
    digest = hashlib.sha256(public_key_pem.encode()).hexdigest()
    return digest[:16]


# ---------------------------------------------------------------------------
# OAuthService
# ---------------------------------------------------------------------------

class OAuthService:
    """RS256 JWT OAuth 2.1 service backed by pgcrypto-encrypted DB tables."""

    # Class-level: survives request-scoped instances; TTL bounds stale deactivated kids.
    # Token revocation remains a separate JTI DB check in validate_token Step 4.
    _public_key_cache: TTLCache = TTLCache(maxsize=10, ttl=300)

    # ------------------------------------------------------------------ keypair

    async def ensure_keypair(self) -> str:
        """Return the ``kid`` of the active keypair, generating one if absent."""
        kid = await self._active_kid()
        if kid:
            return kid

        logger.info("No active auth keypair found — generating new RS256 keypair")
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        public_key = private_key.public_key()
        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        kid = _kid_for_keypair(public_pem)

        async with get_async_session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO auth_keypairs (kid, private_key, public_key, algorithm, is_active, created_at)
                    VALUES (
                        :kid,
                        pgp_sym_encrypt(:private_pem, current_setting('app.master_key')),
                        :public_pem,
                        'RS256',
                        true,
                        now()
                    )
                    ON CONFLICT (kid) DO NOTHING
                    """
                ),
                {"kid": kid, "private_pem": private_pem, "public_pem": public_pem},
            )
            await session.commit()

        logger.info("Generated and stored new keypair with kid=%s", kid)
        return kid

    async def _active_kid(self) -> str | None:
        async with get_async_session() as session:
            row = await session.execute(
                text(
                    "SELECT kid FROM auth_keypairs WHERE is_active = true ORDER BY created_at DESC LIMIT 1"
                )
            )
            result = row.fetchone()
            return result[0] if result else None

    async def _load_private_key(self, kid: str):
        """Decrypt and load the RSA private key for ``kid``."""
        async with get_async_session() as session:
            row = await session.execute(
                text(
                    """
                    SELECT pgp_sym_decrypt(private_key, current_setting('app.master_key'))::TEXT
                    FROM   auth_keypairs
                    WHERE  kid = :kid AND is_active = true
                    """
                ),
                {"kid": kid},
            )
            result = row.fetchone()
        if result is None:
            raise KeypairNotFoundError(f"No active keypair with kid={kid}")
        pem = result[0].encode("utf-8")
        return serialization.load_pem_private_key(pem, password=None, backend=default_backend())

    async def get_jwks(self) -> dict:
        """Return JWKS JSON for ``/.well-known/jwks.json``."""
        async with get_async_session() as session:
            rows = await session.execute(
                text(
                    "SELECT kid, public_key FROM auth_keypairs WHERE is_active = true"
                )
            )
            keypairs = rows.fetchall()

        keys = []
        for kid, public_pem in keypairs:
            pub = serialization.load_pem_public_key(
                public_pem.encode("utf-8"), backend=default_backend()
            )
            pub_numbers = pub.public_numbers()
            n_bytes = pub_numbers.n.to_bytes((pub_numbers.n.bit_length() + 7) // 8, "big")
            e_bytes = pub_numbers.e.to_bytes((pub_numbers.e.bit_length() + 7) // 8, "big")
            keys.append(
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": kid,
                    "n": base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode(),
                    "e": base64.urlsafe_b64encode(e_bytes).rstrip(b"=").decode(),
                }
            )
        return {"keys": keys}

    # ------------------------------------------------------------------ clients

    async def register_client(
        self,
        client_info: OAuthClientInformationFull,
        client_secret: str | None = None,
    ) -> None:
        """Persist a client record; encrypt ``client_secret`` if supplied."""
        async with get_async_session() as session:
            if client_secret:
                await session.execute(
                    text(
                        """
                        INSERT INTO oauth_clients (client_id, client_data, client_secret, created_at)
                        VALUES (
                            :client_id,
                            CAST(:client_data AS JSONB),
                            pgp_sym_encrypt(:secret, current_setting('app.master_key')),
                            now()
                        )
                        ON CONFLICT (client_id) DO UPDATE SET
                            client_data   = EXCLUDED.client_data,
                            client_secret = EXCLUDED.client_secret
                        """
                    ),
                    {
                        "client_id": client_info.client_id,
                        "client_data": client_info.model_dump_json(),
                        "secret": client_secret,
                    },
                )
            else:
                await session.execute(
                    text(
                        """
                        INSERT INTO oauth_clients (client_id, client_data, created_at)
                        VALUES (:client_id, CAST(:client_data AS JSONB), now())
                        ON CONFLICT (client_id) DO UPDATE SET
                            client_data = EXCLUDED.client_data,
                            client_secret = NULL
                        """
                    ),
                    {
                        "client_id": client_info.client_id,
                        "client_data": client_info.model_dump_json(),
                    },
                )
            await session.commit()

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with get_async_session() as session:
            row = await session.execute(
                text("SELECT client_data FROM oauth_clients WHERE client_id = :id"),
                {"id": client_id},
            )
            result = row.fetchone()
        if result is None:
            return None
            
        data = result[0]
        if isinstance(data, dict):
            return OAuthClientInformationFull.model_validate(data)
        return OAuthClientInformationFull.model_validate_json(data)

    async def _verify_client_secret(self, client_id: str, secret: str) -> bool:
        async with get_async_session() as session:
            row = await session.execute(
                text(
                    """
                    SELECT pgp_sym_decrypt(client_secret, current_setting('app.master_key'))::TEXT
                    FROM   oauth_clients
                    WHERE  client_id = :id AND client_secret IS NOT NULL
                    """
                ),
                {"id": client_id},
            )
            result = row.fetchone()
        if result is None:
            return False
        return secrets.compare_digest(result[0], secret)

    async def _client_db_id(self, client_id: str):
        """Return the internal oauth_clients.id UUID for a public client_id."""
        async with get_async_session() as session:
            row = await session.execute(
                text(
                    """
                    SELECT id
                    FROM   oauth_clients
                    WHERE  client_id = :client_id
                      AND  is_active = true
                    """
                ),
                {"client_id": client_id},
            )
            result = row.fetchone()
        if result is None:
            raise InvalidClientError(f"Unknown OAuth client: {client_id}")
        return result[0]

    # ------------------------------------------------------------------ auth code

    async def create_auth_code(
        self,
        client_id: str,
        redirect_uri: str | None,
        scopes: list[str],
        code_challenge: str,
        code_challenge_method: str = "S256",
        extra_claims: dict | None = None,
    ) -> str:
        """Create a short-lived PKCE authorization code.

        ``extra_claims`` (ocat_role / ocat_tenant / ocat_user_id) is persisted
        in ``oauth_auth_codes.extra_claims`` so JWT mint survives a restart
        between authorize and token exchange.
        """
        if not redirect_uri:
            raise InvalidGrantError("redirect_uri is required")
        if not code_challenge:
            raise InvalidGrantError("code_challenge is required")

        code = secrets.token_urlsafe(32)
        expires_at = _now() + timedelta(seconds=AUTH_CODE_TTL)
        db_client_id = await self._client_db_id(client_id)
        claims_json = json.dumps(extra_claims) if extra_claims else None

        async with get_async_session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO oauth_auth_codes
                        (code, client_id, redirect_uri, scopes_granted,
                         code_challenge, code_challenge_method, expires_at,
                         extra_claims)
                    VALUES
                        (:code, :client_id, :redirect_uri, :scopes,
                         :challenge, :method, :expires_at,
                         CAST(:extra_claims AS jsonb))
                    """
                ),
                {
                    "code": code,
                    "client_id": db_client_id,
                    "redirect_uri": redirect_uri,
                    "scopes": " ".join(scopes),
                    "challenge": code_challenge,
                    "method": code_challenge_method,
                    "expires_at": expires_at,
                    "extra_claims": claims_json,
                },
            )
            await session.commit()
        return code

    # ------------------------------------------------------------------ pending auth (restart-safe)

    async def save_pending_auth(self, auth_state: str, data: dict) -> None:
        """Persist pending auth state to DB so it survives a server restart."""
        expires_at = _now() + timedelta(seconds=AUTH_CODE_TTL)
        async with get_async_session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO mcp_pending_auths (auth_state, data, expires_at)
                    VALUES (:state, :data, :expires_at)
                    ON CONFLICT (auth_state) DO UPDATE
                        SET data = EXCLUDED.data,
                            expires_at = EXCLUDED.expires_at
                    """
                ),
                {
                    "state": auth_state,
                    "data": json.dumps(data),
                    "expires_at": expires_at,
                },
            )
            await session.commit()

    async def pop_pending_auth(self, auth_state: str) -> dict | None:
        """Retrieve and atomically delete a pending auth entry from DB.

        Returns ``None`` if the entry is absent or has expired.
        """
        async with get_async_session() as session:
            stmt = text(
                "DELETE FROM mcp_pending_auths WHERE auth_state = :state "
                "RETURNING data, expires_at"
            )
            result = await session.execute(stmt, {"state": auth_state})
            rec = result.fetchone()
            await session.commit()

        if rec is None:
            return None
        data_str, expires_at = rec
        if expires_at.replace(tzinfo=timezone.utc) < _now():
            return None
        return json.loads(data_str)

    async def sweep_db_pending_auths(self) -> int:
        """Delete expired pending auth rows from DB. Returns count deleted."""
        async with get_async_session() as session:
            result = await session.execute(
                text("DELETE FROM mcp_pending_auths WHERE expires_at < now() RETURNING auth_state")
            )
            await session.commit()
            return result.rowcount

    async def get_auth_code(
        self, code: str, client_id: str
    ) -> "AuthorizationCode | None":
        """Load an authorization code from DB for restart recovery.

        Returns ``None`` if the code is absent, already used, or expired.
        """
        async with get_async_session() as session:
            row = await session.execute(
                text(
                    """
                    SELECT c.client_id, ac.redirect_uri, ac.scopes_granted,
                           ac.code_challenge, ac.code_challenge_method,
                           ac.expires_at, ac.used_at, ac.extra_claims
                    FROM   oauth_auth_codes ac
                    JOIN   oauth_clients c ON c.id = ac.client_id
                    WHERE  ac.code = :code AND c.client_id = :client_id
                    """
                ),
                {"code": code, "client_id": client_id},
            )
            rec = row.fetchone()

        if rec is None:
            return None
        (
            pub_client_id,
            redirect_uri,
            scopes_str,
            challenge,
            method,
            expires_at,
            used_at,
            extra_claims,
        ) = rec
        if used_at is not None:
            return None
        if expires_at.replace(tzinfo=timezone.utc) < _now():
            return None
        auth = AuthorizationCode(
            code=code,
            scopes=(scopes_str or "").split(),
            expires_at=expires_at.replace(tzinfo=timezone.utc).timestamp(),
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=bool(redirect_uri),
            client_id=pub_client_id,
            code_challenge=challenge,
            code_challenge_method=method or "S256",
        )
        # Stash DB claims for exchange_authorization_code restart recovery
        if extra_claims:
            if isinstance(extra_claims, str):
                try:
                    extra_claims = json.loads(extra_claims)
                except Exception:
                    extra_claims = None
            if isinstance(extra_claims, dict) and extra_claims:
                # Attach as transient attribute (AuthorizationCode is a pydantic model)
                object.__setattr__(auth, "_extra_claims", extra_claims)
        return auth

    async def consume_auth_code(self, code: str) -> None:
        """Mark an authorization code as used in the DB to prevent replay."""
        async with get_async_session() as session:
            await session.execute(
                text(
                    "UPDATE oauth_auth_codes SET used_at = now() "
                    "WHERE code = :code AND used_at IS NULL"
                ),
                {"code": code},
            )
            await session.commit()

    # token issuance

    async def _mint_jwt(
        self,
        kid: str,
        subject: str,
        client_id: str,
        scopes: list[str],
        ttl: int,
        token_type: str = "access",
        org_id: str = "default",
        extra_claims: dict | None = None,
    ) -> tuple[str, str, datetime]:
        """Sign a JWT and persist the JTI before returning.

        Returns ``(jwt_string, jti, expires_at)``.

        ``org_id`` is embedded as a custom claim so the executor can resolve
        the right ``InstanceBinding`` at call time. Single-tenant deployments
        and legacy tokens use ``"default"``.
        """
        import time
        import jwt as _jwt  # PyJWT

        private_key = await self._load_private_key(kid)
        jti = str(uuid.uuid4())
        now_ts = int(time.time())
        exp_ts = now_ts + ttl
        expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)

        payload = {
            "iss": "whiskers",
            "sub": subject,
            "aud": client_id,
            "iat": now_ts,
            "exp": exp_ts,
            "jti": jti,
            "scope": " ".join(scopes),
            "token_type": token_type,
            "org_id": org_id,
        }
        if extra_claims:
            for k, v in extra_claims.items():
                if k not in payload:
                    payload[k] = v
        pem_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        token_str = _jwt.encode(payload, pem_bytes, algorithm="RS256", headers={"kid": kid})

        # Persist JTI before returning — avoids TOCTOU race on fast validators
        issued_at = datetime.fromtimestamp(now_ts, tz=timezone.utc)
        db_client_id = await self._client_db_id(client_id)
        async with get_async_session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO oauth_tokens
                        (jti, client_id, token_type, scopes, issued_at, expires_at)
                    VALUES
                        (:jti, :client_id, :token_type, :scopes, :issued_at, :expires_at)
                    """
                ),
                {
                    "jti": jti,
                    "client_id": db_client_id,
                    "token_type": token_type,
                    "scopes": " ".join(scopes),
                    "issued_at": issued_at,
                    "expires_at": expires_at,
                },
            )
            await session.commit()

        return token_str, jti, expires_at

    async def ensure_internal_client(self, client_id: str, scopes: str = "whiskers") -> None:
        """Upsert a first-party internal OAuth client (no redirect_uri required)."""
        existing = await self.get_client(client_id)
        if existing is not None:
            return
        client = OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=["http://localhost/admin/callback"],
            grant_types=["refresh_token"],
            response_types=[],
            token_endpoint_auth_method="none",
            scope=scopes,
        )
        await self.register_client(client)
        logger.debug("Registered internal OAuth client %r", client_id)

    async def _issue_token_pair(
        self, client_id: str, scopes: list[str], extra_claims: dict | None = None
    ) -> dict:
        """Issue an access + refresh token pair."""
        kid = await self.ensure_keypair()
        access_token, _, expires_at = await self._mint_jwt(
            kid, subject=client_id, client_id=client_id,
            scopes=scopes, ttl=ACCESS_TOKEN_TTL, token_type="access",
            extra_claims=extra_claims,
        )
        refresh_token, _, _ = await self._mint_jwt(
            kid, subject=client_id, client_id=client_id,
            scopes=scopes, ttl=REFRESH_TOKEN_TTL, token_type="refresh",
            extra_claims=extra_claims,
        )
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": ACCESS_TOKEN_TTL,
            "expires_at": expires_at,
            "scope": " ".join(scopes),
        }

    async def client_credentials_grant(
        self, client_id: str, client_secret: str, scopes: list[str]
    ) -> dict:
        """Server-to-server grant."""
        if not await self._verify_client_secret(client_id, client_secret):
            raise InvalidClientError("Invalid client credentials")
        return await self._issue_token_pair(client_id, scopes)

    async def refresh_grant(self, refresh_token_str: str) -> dict:
        """Rotate the refresh token; revoke the old one."""
        payload = await self.validate_token(refresh_token_str)
        if payload.get("token_type") not in (None, "refresh"):
            raise InvalidGrantError("Token is not a refresh token")

        client_id = payload["client_id"]
        scopes = payload["scopes"]
        old_jti = payload["jti"]

        # Revoke old refresh token
        await self.revoke_token(old_jti)
        return await self._issue_token_pair(client_id, scopes)

    # ------------------------------------------------------------------ validation

    async def validate_token(self, token_str: str) -> dict:
        """Decode, verify, and return the token payload dict.

        Raises ``ExpiredTokenError``, ``RevokedTokenError``, or ``InvalidTokenError``.
        """
        import jwt as _jwt

        # Step 1: decode header to get kid (unverified)
        try:
            header = _jwt.get_unverified_header(token_str)
        except Exception as exc:
            raise InvalidTokenError(f"Cannot decode JWT header: {exc}") from exc

        kid = header.get("kid")

        # Step 2: load matching public key from cache or DB
        public_key = self._public_key_cache.get(kid)
        if public_key is None:
            async with get_async_session() as session:
                row = await session.execute(
                    text(
                        "SELECT public_key FROM auth_keypairs WHERE kid = :kid AND is_active = true"
                    ),
                    {"kid": kid},
                )
                result = row.fetchone()

            if result is None:
                raise KeypairNotFoundError(f"No active keypair for kid={kid}")

            public_key = serialization.load_pem_public_key(
                result[0].encode("utf-8"), backend=default_backend()
            )
            self._public_key_cache[kid] = public_key

        # Step 3: verify signature + expiry
        try:
            payload = _jwt.decode(
                token_str,
                public_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        except _jwt.ExpiredSignatureError as exc:
            raise ExpiredTokenError("Token has expired") from exc
        except _jwt.InvalidTokenError as exc:
            raise InvalidTokenError(f"JWT validation failed: {exc}") from exc

        # Step 4: JTI blocklist check (single indexed read)
        jti = payload.get("jti")
        if jti:
            async with get_async_session() as session:
                row = await session.execute(
                    text(
                        "SELECT revoked_at FROM oauth_tokens WHERE jti = :jti"
                    ),
                    {"jti": jti},
                )
                jti_row = row.fetchone()
            if jti_row is None:
                raise InvalidTokenError("Token JTI not found — possibly pre-issued")
            if jti_row[0] is not None:
                raise RevokedTokenError("Token has been revoked")

        res = {
            "jti": jti,
            "client_id": payload.get("aud"),
            "scopes": payload.get("scope", "").split(),
            "sub": payload.get("sub"),
            "exp": payload.get("exp"),
            "token_type": payload.get("token_type", "access"),
            "org_id": payload.get("org_id", "default"),
        }
        for k, v in payload.items():
            if k not in res and k not in ("aud", "scope"):
                res[k] = v
        return res

    async def revoke_token(self, jti: str) -> None:
        """Mark a token JTI as revoked."""
        async with get_async_session() as session:
            await session.execute(
                text(
                    "UPDATE oauth_tokens SET revoked_at = now() WHERE jti = :jti"
                ),
                {"jti": jti},
            )
            await session.commit()


# ---------------------------------------------------------------------------
# FastMCP adapter — thin wrapper so FastMCP's auth= kwarg still works
# ---------------------------------------------------------------------------

class OAuthService_FastMCPProvider(OAuthProvider):
    """
    Thin FastMCP OAuthProvider adapter that delegates to ``OAuthService``.

    This adapter handles the MCP inbound token lifecycle only (Layer 1).
    External OAuth relay (Layer 2) is handled by ``ExternalOAuthRelay``
    in oauth/oauth_relay.py.

    ``mcp_bearer_tokens`` (from core_011) is used to persist opaque
    bearer tokens for the FastMCP adapter's db_load_token path.
    """

    def __init__(
        self,
        service: OAuthService,
        base_url: str = "http://localhost:10000",
        valid_scopes: list[str] | None = None,
        # Pending Whiskers Agent relay state (authorise redirects before relay is ready)
        _pending_auths: dict | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=valid_scopes or ["whiskers"],
            ),
        )
        self._svc = service
        # In-memory pending auth state (keyed by auth_state → metadata)
        self._pending_auths: dict[str, dict] = _pending_auths or {}
        self._pending_ts: dict[str, float] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._auth_code_claims: dict[str, dict] = {}
        self._access_tokens: dict[str, AccessToken] = {}

    # -- Client management ---------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await self._svc.get_client(client_id)

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        await self._svc.register_client(client_info)

    async def auto_register_client(
        self, client_id: str, redirect_uri_str: str
    ) -> None:
        """Auto-register (or update) a client that skips POST /register."""
        try:
            parsed_uri = AnyUrl(redirect_uri_str)
        except Exception:
            logger.warning(
                "Auto-registration skipped: invalid redirect_uri %r", redirect_uri_str
            )
            return

        scopes_list = self.client_registration_options.valid_scopes or ["whiskers"]
        scopes_str = " ".join(scopes_list)

        existing = await self.get_client(client_id)
        if existing and existing.redirect_uris and parsed_uri in existing.redirect_uris:
            if existing.scope == scopes_str:
                return

        redirect_uris = list(existing.redirect_uris) if existing and existing.redirect_uris else []
        if parsed_uri not in redirect_uris:
            redirect_uris.append(parsed_uri)

        client = OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=redirect_uris,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope=scopes_str,
        )
        await self.register_client(client)
        logger.info("Auto-registered OAuth client %r with scope %r", client_id, scopes_str)

    # -- Authorization code flow --------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Store pending auth and return the relay authorize URL."""
        # This method is called by FastMCP on GET /authorize.
        # The relay URL is built by ExternalOAuthRelay.build_authorize_url()
        # in oauth_routes.py.  We stash the pending state here so the relay
        # can look it up after the callback.
        auth_state = secrets.token_urlsafe(32)
        model_extra = params.model_extra if hasattr(params, "model_extra") else None
        code_challenge_method = (
            getattr(params, "code_challenge_method", None)
            or (model_extra.get("code_challenge_method") if model_extra else None)
            or "S256"
        )
        pending_data: dict = {
            "client_id": client.client_id,
            "code_challenge": params.code_challenge,
            "code_challenge_method": code_challenge_method,
            "redirect_uri": str(params.redirect_uri) if params.redirect_uri else None,
            "scopes": list(params.scopes or []),
            "state": params.state,
        }
        # Persist session role at authorize time so callback can union scopes even
        # when the browser cookie is gone (Bug B). Callers may set these via
        # set_pending_role / attach_session_to_pending before authorize returns;
        # also accept model_extra hooks when present.
        if model_extra:
            role = model_extra.get("whiskers_role") or model_extra.get("ocat_role")
            if role:
                pending_data["whiskers_role"] = role
                pending_data["ocat_role"] = role
            user_id = model_extra.get("whiskers_user_id") or model_extra.get("ocat_user_id")
            if user_id:
                pending_data["whiskers_user_id"] = user_id
                pending_data["ocat_user_id"] = user_id
            tenant = model_extra.get("whiskers_tenant") if model_extra.get("whiskers_tenant") is not None else model_extra.get("ocat_tenant")
            if tenant is not None:
                pending_data["whiskers_tenant"] = tenant
                pending_data["ocat_tenant"] = tenant
        self._pending_auths[auth_state] = pending_data
        self._pending_ts[auth_state] = _now().timestamp()
        self._sweep_pending_auths()
        asyncio.create_task(self._svc.sweep_db_pending_auths())
        # Also persist to DB so the state survives a server restart (Bug 1 fix).
        await self._svc.save_pending_auth(auth_state, pending_data)
        # Return a relative path; FastMCP prepends base_url to build the redirect.
        # This lands on the connect page (/oauth/connect/{auth_state}) rather than a 404.
        return f"oauth/connect/{auth_state}"

    def _sweep_pending_auths(self, ttl: float = AUTH_CODE_TTL) -> int:
        """Evict in-memory pending auths older than ttl seconds. Returns count removed."""
        # Evict expired auth codes + claims
        now_ts = _now().timestamp()
        auth_codes = getattr(self, "_auth_codes", None)
        if auth_codes is not None:
            expired_codes = [
                code for code, ac in auth_codes.items()
                if getattr(ac, "expires_at", 0) < now_ts
            ]
            auth_code_claims = getattr(self, "_auth_code_claims", None)
            for code in expired_codes:
                auth_codes.pop(code, None)
                if auth_code_claims is not None:
                    auth_code_claims.pop(code, None)

        cutoff = _now().timestamp() - ttl
        stale = [s for s, ts in self._pending_ts.items() if ts < cutoff]
        for s in stale:
            self._pending_auths.pop(s, None)
            self._pending_ts.pop(s, None)
        return len(stale)

    def attach_session_role_to_pending(
        self, auth_state: str, role: str | None, user_id: str | None = None, tenant_id: int | None = None
    ) -> None:
        """Stamp whiskers_role/ocat_role / whiskers_user_id/ocat_user_id / whiskers_tenant/ocat_tenant onto an in-memory pending auth."""
        pending = self._pending_auths.get(auth_state)
        if pending is None:
            return
        if role:
            pending["whiskers_role"] = role
            pending["ocat_role"] = role
        if user_id:
            pending["whiskers_user_id"] = user_id
            pending["ocat_user_id"] = user_id
        if tenant_id is not None:
            pending["whiskers_tenant"] = tenant_id
            pending["ocat_tenant"] = tenant_id

    async def attach_session_role_to_pending_async(
        self, auth_state: str, role: str | None, user_id: str | None = None, tenant_id: int | None = None
    ) -> None:
        """Stamp role on pending (memory + DB) so it survives to callback without cookie."""
        self.attach_session_role_to_pending(auth_state, role, user_id, tenant_id)
        pending = self._pending_auths.get(auth_state)
        if pending is not None:
            try:
                await self._svc.save_pending_auth(auth_state, pending)
            except Exception:
                logger.exception("Failed to persist pending role for state=%s", auth_state)

    async def _complete_authorization(
        self, auth_state: str, provider: str, extra_scopes: list[str] | None = None
    ) -> str:
        """Called from the callback route after the external provider returns.

        Creates an MCP auth code and returns the redirect URL for the MCP client.

        Scope merge is UNION of: client-requested (pending) ∪ role floor
        (playground_mcp_scopes(ocat_role)) ∪ extra_scopes from the callback.
        Role is stamped into the auth-code record as extra_claims for JWT mint.
        """
        pending = self._pending_auths.pop(auth_state, None)
        self._pending_ts.pop(auth_state, None)
        if pending is None:
            # Bug 1 fallback: recover from DB when in-memory state was lost on restart.
            pending = await self._svc.pop_pending_auth(auth_state)
        if pending is None:
            raise InvalidGrantError(f"Unknown auth_state: {auth_state}")

        role = pending.get("whiskers_role") or pending.get("ocat_role")
        role_scopes: list[str] = []
        if role:
            try:
                from core.api_key_management.scopes import playground_mcp_scopes
                role_scopes = list(playground_mcp_scopes(role))
            except Exception:
                role_scopes = []

        scopes = sorted(
            set(pending.get("scopes") or [])
            | set(role_scopes)
            | set(extra_scopes or [])
        )

        extra_claims: dict = {}
        if role:
            extra_claims["whiskers_role"] = role
            extra_claims["ocat_role"] = role
        user_id = pending.get("whiskers_user_id") or pending.get("ocat_user_id")
        if user_id:
            extra_claims["whiskers_user_id"] = user_id
            extra_claims["ocat_user_id"] = user_id
        tenant_val = pending.get("whiskers_tenant") if pending.get("whiskers_tenant") is not None else pending.get("ocat_tenant")
        if tenant_val is not None:
            extra_claims["whiskers_tenant"] = tenant_val
            extra_claims["ocat_tenant"] = tenant_val

        code = await self._svc.create_auth_code(
            client_id=pending["client_id"],
            redirect_uri=pending.get("redirect_uri"),
            scopes=scopes,
            code_challenge=pending["code_challenge"],
            code_challenge_method=pending.get("code_challenge_method", "S256"),
            extra_claims=extra_claims or None,
        )
        expires_at = (_now() + timedelta(seconds=AUTH_CODE_TTL)).timestamp()

        # Store for exchange_authorization_code lookup (+ role claims)
        auth_code = AuthorizationCode(
            code=code,
            scopes=scopes,
            expires_at=expires_at,
            redirect_uri=pending.get("redirect_uri"),
            redirect_uri_provided_explicitly=bool(pending.get("redirect_uri")),
            client_id=pending["client_id"],
            code_challenge=pending["code_challenge"],
            code_challenge_method=pending.get("code_challenge_method", "S256"),
        )
        self._auth_codes[code] = auth_code
        if extra_claims:
            self._auth_code_claims[code] = extra_claims

        redirect_base = pending.get("redirect_uri", "")
        mcp_state = pending.get("state", "")
        separator = "&" if "?" in redirect_base else "?"
        return f"{redirect_base}{separator}code={_urlquote(code, safe='')}&state={_urlquote(mcp_state, safe='')}"

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        # This is called by FastMCP at POST /token.
        # The auth code was already validated against the DB in load_authorization_code.
        # Consume it now so it cannot be replayed.
        self._auth_codes.pop(authorization_code.code, None)
        # Bug 3 fix: mark the DB record as used to prevent replay attacks.
        await self._svc.consume_auth_code(authorization_code.code)
        # In-memory claims first; fall back to DB-hydrated claims on restart
        claims = self._auth_code_claims.pop(authorization_code.code, {}) or {}
        if not claims:
            claims = getattr(authorization_code, "_extra_claims", None) or {}
        tokens = await self._svc._issue_token_pair(
            client.client_id,
            list(authorization_code.scopes or []),
            extra_claims=claims or None,
        )
        return OAuthToken(
            access_token=tokens["access_token"],
            token_type="bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=tokens.get("refresh_token"),
            scope=" ".join(authorization_code.scopes or []),
        )

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        # Fast path: in-memory cache.
        cached = self._auth_codes.get(authorization_code)
        if cached:
            return cached
        # Bug 2 fallback: recover from DB when in-memory cache was lost on restart.
        from_db = await self._svc.get_auth_code(authorization_code, client.client_id)
        if from_db:
            self._auth_codes[authorization_code] = from_db  # warm cache
            # Rehydrate role/tenant claims lost with the in-memory dict on restart
            db_claims = getattr(from_db, "_extra_claims", None)
            if isinstance(db_claims, dict) and db_claims:
                self._auth_code_claims[authorization_code] = db_claims
        return from_db

    async def load_access_token(self, token: str) -> AccessToken | None:
        # Check in-memory cache first (fast path)
        cached = self._access_tokens.get(token)
        if cached:
            return cached

        # Validate via service (handles DB JTI check)
        try:
            payload = await self._svc.validate_token(token)
        except (InvalidTokenError, ExpiredTokenError, RevokedTokenError, KeypairNotFoundError) as exc:
            if token.startswith("octk_"):
                # Fallback to API key store lookup for tokens with prefix 'octk_'
                from db_layer.api_key_store import lookup_active_by_token
                row = await lookup_active_by_token(token)
                if row:
                    from core.api_key_management.scopes import resolve_api_key_scopes
                    try:
                        from core.context import current_org_id as _current_org_id
                        _current_org_id.set("default")
                    except Exception as ctx_exc:
                        logger.debug("Failed to set current_org_id for API key: %s", ctx_exc)
                    try:
                        from core.context import current_tenant_id as _current_tenant_id
                        _current_tenant_id.set(int(row.get("tenant_id") or 1))
                    except Exception as ctx_exc:
                        logger.debug("Failed to set current_tenant_id for API key: %s", ctx_exc)
                    expires_at = int(row["expires_at"].timestamp()) if row.get("expires_at") else None
                    # Do NOT cache API-key tokens: they may never expire, so a
                    # cached entry would keep authenticating after a revoke/delete
                    # until restart. lookup_active_by_token is an O(1) indexed
                    # hash query, so re-checking the store per request is cheap and
                    # makes revocation take effect immediately.
                    access = OcatAccessToken(
                        token=token,
                        client_id=f"api-key:{row['key_id']}",
                        scopes=resolve_api_key_scopes(row),
                        expires_at=expires_at,
                        # API keys carry no user role; force_execute stays role-default False.
                        claims=None,
                    )
                    return access
                return None

            logger.warning("Failed to validate access token: %s", exc)
            return None

        # Propagate org_id from the JWT into the per-request contextvar so the
        # executor can resolve the right InstanceBinding at call time.
        try:
            from core.context import current_org_id as _current_org_id
            _current_org_id.set(payload.get("org_id") or "default")
        except Exception as exc:
            logger.debug("Failed to set current_org_id from token payload: %s", exc)
        try:
            from core.context import current_tenant_id as _current_tenant_id
            _raw_tenant = payload.get("whiskers_tenant") if payload.get("whiskers_tenant") is not None else payload.get("ocat_tenant")
            _current_tenant_id.set(int(_raw_tenant or 1))
        except Exception as exc:
            logger.debug("Failed to set current_tenant_id from token payload: %s", exc)

        import time
        access = OcatAccessToken(
            token=token,
            client_id=payload["client_id"],
            scopes=payload["scopes"],
            expires_at=payload.get("exp"),
            claims=payload,
        )
        self._access_tokens[token] = access
        return access

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
        token_type_hint: str | None = None,
    ) -> None:
        # token.token is the raw JWT; extract jti
        try:
            # Fully verify JWT signature before revocation to prevent forged tokens
            # from triggering revocation (Layer 1).
            payload = await self._svc.validate_token(token.token)
            jti = payload.get("jti")
            if jti:
                await self._svc.revoke_token(jti)
        except (InvalidTokenError, ExpiredTokenError, RevokedTokenError) as exc:
            logger.warning("revoke_token: signature/validation failed: %s", exc)
        except Exception as exc:
            logger.warning("revoke_token: could not extract jti: %s", exc)

        # Remove from in-memory cache
        self._access_tokens.pop(token.token, None)
