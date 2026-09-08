"""Unit tests for ElevationService."""

import pyotp
import pytest
import time
from argon2 import PasswordHasher
from plugins.cat_terminal_relay_plugin.services.elevation_service import ElevationService


class FakeVault:
    """A fake async VaultService for testing."""

    def __init__(self, **kv):
        self._kv = kv

    async def get(self, plugin_id, key_name):
        return self._kv.get(key_name)

    async def exists(self, plugin_id, key_name):
        return key_name in self._kv


@pytest.mark.asyncio
async def test_totp_success_mints_token():
    seed = pyotp.random_base32()
    vault = FakeVault(TOTP_SEED=seed)
    svc = ElevationService(method="totp", ttl_seconds=300, vault=vault)
    totp_code = pyotp.TOTP(seed).now()

    res = await svc.verify_and_mint("session_1", "alice", totp=totp_code)
    assert res["status"] == "ok"
    assert "token" in res
    assert res["ttl"] == 300
    assert abs(res["expires_at"] - (time.time() + 300)) < 2.0

    assert svc.is_elevated("session_1") is True
    assert abs(svc.peek("session_1") - res["expires_at"]) < 0.1


@pytest.mark.asyncio
async def test_totp_wrong_code_lockout():
    seed = pyotp.random_base32()
    vault = FakeVault(TOTP_SEED=seed)
    # 3 attempts allowed, lockout of 300s
    svc = ElevationService(method="totp", max_attempts=3, lockout_seconds=300, vault=vault)

    # Attempt 1: wrong code
    res = await svc.verify_and_mint("session_2", "alice", totp="000000")
    assert res["status"] == "error"
    assert res["error"] == "invalid_factor"
    assert res["attempts_remaining"] == 2

    # Attempt 2: wrong code
    res = await svc.verify_and_mint("session_2", "alice", totp="000000")
    assert res["status"] == "error"
    assert res["error"] == "invalid_factor"
    assert res["attempts_remaining"] == 1

    # Attempt 3: wrong code -> locked
    res = await svc.verify_and_mint("session_2", "alice", totp="000000")
    assert res["status"] == "error"
    assert res["error"] == "locked"
    assert "locked_until" in res
    assert res["locked_until"] > time.time()

    # Subsequent check while locked should not decrement further and return locked
    res = await svc.verify_and_mint("session_2", "alice", totp="000000")
    assert res["status"] == "error"
    assert res["error"] == "locked"


@pytest.mark.asyncio
async def test_totp_replay_guard():
    seed = pyotp.random_base32()
    vault = FakeVault(TOTP_SEED=seed)
    svc = ElevationService(method="totp", vault=vault)
    totp_code = pyotp.TOTP(seed).now()

    # First: ok
    res = await svc.verify_and_mint("session_3", "alice", totp=totp_code)
    assert res["status"] == "ok"

    # Second: replay error (failed attempt, count decrements)
    res = await svc.verify_and_mint("session_3", "alice", totp=totp_code)
    assert res["status"] == "error"
    assert res["error"] == "replay"
    assert res["attempts_remaining"] == 2


@pytest.mark.asyncio
async def test_password_method():
    ph = PasswordHasher()
    pwd_hash = ph.hash("mysecretpassword")
    vault = FakeVault(PASSWORD_HASH=pwd_hash)
    svc = ElevationService(method="password", vault=vault)

    # Correct password: ok
    res = await svc.verify_and_mint("session_4", "alice", password="mysecretpassword")
    assert res["status"] == "ok"

    # Reset/clear elevation to test wrong password
    svc.clear("session_4")
    assert svc.is_elevated("session_4") is False

    # Wrong password: invalid_factor
    res = await svc.verify_and_mint("session_4", "alice", password="wrongpassword")
    assert res["status"] == "error"
    assert res["error"] == "invalid_factor"


@pytest.mark.asyncio
async def test_both_method():
    seed = pyotp.random_base32()
    ph = PasswordHasher()
    pwd_hash = ph.hash("supersecure")
    vault = FakeVault(TOTP_SEED=seed, PASSWORD_HASH=pwd_hash)
    svc = ElevationService(method="both", vault=vault)

    totp_code = pyotp.TOTP(seed).now()

    # Missing both: invalid_factor
    res = await svc.verify_and_mint("session_5", "alice")
    assert res["status"] == "error"
    assert res["error"] == "invalid_factor"

    # Missing password: invalid_factor
    res = await svc.verify_and_mint("session_5", "alice", totp=totp_code)
    assert res["status"] == "error"
    assert res["error"] == "invalid_factor"

    # Correct both: ok
    svc2 = ElevationService(method="both", vault=vault)
    totp_code2 = pyotp.TOTP(seed).now()
    res = await svc2.verify_and_mint("session_6", "alice", totp=totp_code2, password="supersecure")
    assert res["status"] == "ok"


@pytest.mark.asyncio
async def test_not_provisioned():
    vault = FakeVault()
    svc = ElevationService(method="both", vault=vault)
    res = await svc.verify_and_mint("session_7", "alice", totp="123456", password="foo")
    assert res["status"] == "error"
    assert res["error"] == "not_provisioned"


@pytest.mark.asyncio
async def test_ttl_expiry():
    seed = pyotp.random_base32()
    vault = FakeVault(TOTP_SEED=seed)
    svc = ElevationService(method="totp", ttl_seconds=0, vault=vault)
    totp_code = pyotp.TOTP(seed).now()

    res = await svc.verify_and_mint("session_8", "alice", totp=totp_code)
    assert res["status"] == "ok"
    assert svc.is_elevated("session_8") is False


@pytest.mark.asyncio
async def test_clear_drops_elevation():
    seed = pyotp.random_base32()
    vault = FakeVault(TOTP_SEED=seed)
    svc = ElevationService(method="totp", vault=vault)
    totp_code = pyotp.TOTP(seed).now()

    res = await svc.verify_and_mint("session_9", "alice", totp=totp_code)
    assert res["status"] == "ok"
    assert svc.is_elevated("session_9") is True

    svc.clear("session_9")
    assert svc.is_elevated("session_9") is False
    assert svc.peek("session_9") is None
