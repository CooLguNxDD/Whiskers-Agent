"""Unit tests for OAuthService public-key TTL cache on validate_token."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from oauth.oauth_service import OAuthService


def _make_rs256_pair():
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_key, public_pem


@pytest.fixture(autouse=True)
def _clear_pubkey_cache():
    OAuthService._public_key_cache.clear()
    yield
    OAuthService._public_key_cache.clear()


@pytest.mark.asyncio
async def test_validate_token_caches_public_key_and_skips_second_keypair_query():
    """Second validate_token for same kid must not re-query auth_keypairs."""
    private_key, public_pem = _make_rs256_pair()
    kid = "cache-test-kid"
    jti = "cache-test-jti"
    now = int(time.time())
    token = pyjwt.encode(
        {
            "jti": jti,
            "aud": "test-client",
            "scope": "whiskers",
            "sub": "test-client",
            "exp": now + 3600,
            "token_type": "access",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )

    keypair_queries = {"n": 0}
    jti_queries = {"n": 0}

    class _Result:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    async def _execute(stmt, params=None):
        sql = str(stmt)
        if "auth_keypairs" in sql:
            keypair_queries["n"] += 1
            return _Result((public_pem,))
        if "oauth_tokens" in sql:
            jti_queries["n"] += 1
            # revoked_at is None → not revoked
            return _Result((None,))
        return _Result(None)

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(side_effect=_execute)
    mock_session.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_session():
        yield mock_session

    with patch("oauth.oauth_service.get_async_session", _fake_session):
        svc = OAuthService()
        payload1 = await svc.validate_token(token)
        payload2 = await svc.validate_token(token)

    assert payload1["jti"] == jti
    assert payload2["jti"] == jti
    assert keypair_queries["n"] == 1, "public key should be loaded from DB once"
    assert jti_queries["n"] == 2, "JTI revoke check still runs every validation"
    assert kid in OAuthService._public_key_cache
