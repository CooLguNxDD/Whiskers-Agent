"""Unit tests for analytics WS handshake auth (no-token and missing-scope rejection paths)."""

import pytest
from unittest.mock import MagicMock

from core.telemetry.auth import authenticate_handshake


async def test_authenticate_handshake_returns_none_when_no_token():
    """No bearer token (neither query nor header) -> reject with None."""
    ws = MagicMock()
    ws.query_params.get.return_value = None
    ws.headers.get.return_value = ""
    result = await authenticate_handshake(ws, required_scope="analytics:read")
    assert result is None


async def test_authenticate_handshake_returns_none_missing_scope(monkeypatch):
    """Valid token but missing required scope -> reject with None (covers scope branch)."""
    monkeypatch.delenv("CAT_TERMINAL_DEV_AUTH", raising=False)

    class _FakeSvc:
        async def validate_token(self, token: str):
            return {"scopes": ["other:read"], "sub": "some-user"}

    class _FakeProvider:
        _svc = _FakeSvc()

    monkeypatch.setattr("core.context.oauth_provider", _FakeProvider(), raising=False)

    ws = MagicMock()
    ws.query_params.get.return_value = "valid-looking-token"
    ws.headers.get.return_value = ""
    result = await authenticate_handshake(ws, required_scope="analytics:read")
    assert result is None
