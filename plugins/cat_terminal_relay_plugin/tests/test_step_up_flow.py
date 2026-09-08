"""
Integration test for §11 Step-Up Auth — exercises the REAL service singletons
end to end at the Python layer (no mocked ElevationService / SessionRegistry).

Wires the cookie-authed REST elevate handler → ElevationService.verify_and_mint →
ide_registry control-frame push → session kill-hook, asserting:
  * a valid TOTP mints a capability, pushes ``elevation_granted`` to the host
    control leg, and flips ``is_elevated`` true;
  * exhausting ``max_attempts`` locks the session, kills it through
    ``kill_relay_session``, and the kill-hook clears the elevation state.

Uses fake vault + fake control WS only at the trust boundaries (credential store
and the host socket); everything in between is the production object graph.
"""

import json

import pyotp
import pytest

from plugins.cat_terminal_relay_plugin.routes import control_routes as cr
from plugins.cat_terminal_relay_plugin.services import (
    elevation_service,
    ide_registry,
    session_registry,
)


class _FakeVault:
    """Async vault stub holding provisioned step-up secrets in memory."""

    def __init__(self, **kv: str) -> None:
        self._kv = kv

    async def get(self, plugin_id: str, key_name: str):
        return self._kv.get(key_name)

    async def exists(self, plugin_id: str, key_name: str) -> bool:
        return key_name in self._kv


class _FakeControlWS:
    """Stand-in for the host control leg; records pushed JSON frames."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


class _FakeRequest:
    """Minimal Starlette-Request shim for the handlers."""

    def __init__(self, body=None, path_params=None) -> None:
        self._body = body or {}
        self.path_params = path_params or {}
        self.cookies: dict[str, str] = {}

    async def json(self):
        return self._body


@pytest.fixture
def dev_auth(monkeypatch):
    """Enable the dev-auth seam so the cookie helper returns a fixed subject."""
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")

    def _bind(subject: str):
        monkeypatch.setenv("CAT_TERMINAL_DEV_SUBJECT", subject)
    return _bind


@pytest.fixture(autouse=True)
def restore_method():
    """Pin the configured method to 'totp' for the test and restore after."""
    prev = elevation_service.method
    elevation_service.method = "totp"
    yield
    elevation_service.method = prev


def _body(resp):
    return json.loads(resp.body)


@pytest.mark.asyncio
async def test_valid_totp_grants_and_pushes_control_frame(dev_auth, monkeypatch):
    subject = "alice-grant"
    dev_auth(subject)
    seed = pyotp.random_base32()
    monkeypatch.setattr(elevation_service, "vault", _FakeVault(TOTP_SEED=seed))

    ide_id = "ide-grant"
    ws = _FakeControlWS()
    await ide_registry.register(ide_id, ws, subject)
    session = session_registry.create(subject, ide_id)

    try:
        code = pyotp.TOTP(seed).now()
        resp = await cr.elevate_session(
            _FakeRequest({"totp": code}, {"session_id": session.session_id})
        )
        assert resp.status_code == 200
        body = _body(resp)
        assert body["status"] == "ok"
        assert isinstance(body["expires_at"], (int, float))

        # Control frame pushed to the host leg.
        assert len(ws.sent) == 1
        frame = json.loads(ws.sent[0])
        assert frame["type"] == "elevation_granted"
        assert frame["session_id"] == session.session_id

        # Capability is live.
        assert elevation_service.is_elevated(session.session_id) is True
    finally:
        elevation_service.clear(session.session_id)
        await session_registry.kill(session.session_id)
        ide_registry._hosts.pop(ide_id, None)


@pytest.mark.asyncio
async def test_lockout_kills_session_and_clears_elevation(dev_auth, monkeypatch):
    subject = "alice-lock"
    dev_auth(subject)
    seed = pyotp.random_base32()
    monkeypatch.setattr(elevation_service, "vault", _FakeVault(TOTP_SEED=seed))
    elevation_service.max_attempts = 3

    ide_id = "ide-lock"
    ws = _FakeControlWS()
    await ide_registry.register(ide_id, ws, subject)
    session = session_registry.create(subject, ide_id)
    sid = session.session_id

    real = pyotp.TOTP(seed).now()
    wrong = "000000" if real != "000000" else "111111"

    try:
        last = None
        for _ in range(elevation_service.max_attempts):
            last = await cr.elevate_session(
                _FakeRequest({"totp": wrong}, {"session_id": sid})
            )
        # Final attempt locks → handler kills the session.
        assert last.status_code == 403
        assert _body(last)["error"] == "locked"

        # Session is gone and elevation state was cleared by the kill-hook.
        assert session_registry.get(sid) is None
        assert elevation_service.is_elevated(sid) is False
    finally:
        elevation_service.clear(sid)
        ide_registry._hosts.pop(ide_id, None)
