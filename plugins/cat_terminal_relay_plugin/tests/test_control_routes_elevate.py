import json as _json
import pytest
from starlette.responses import JSONResponse
from plugins.cat_terminal_relay_plugin.routes import control_routes as cr

class FakeRequest:
    def __init__(self, body=None, path_params=None, cookies=None):
        self._body = body or {}
        self.path_params = path_params or {}
        self.cookies = cookies or {}
    async def json(self): return self._body

class FakeSession:
    def __init__(self, session_id, subject, ide_id):
        self.session_id = session_id
        self.subject = subject
        self.ide_id = ide_id

@pytest.fixture(autouse=True)
def dev_subject(monkeypatch):
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")
    monkeypatch.setenv("CAT_TERMINAL_DEV_SUBJECT", "alice")


@pytest.mark.asyncio
async def test_elevate_ok(monkeypatch):
    monkeypatch.setattr(cr.session_registry, "get", lambda sid: FakeSession("s1", "alice", "ide1"))

    async def mock_verify_and_mint(session_id, subject, totp=None, password=None):
        return {"status": "ok", "token": "t", "expires_at": 123.0, "ttl": 300}

    monkeypatch.setattr(cr.elevation_service, "verify_and_mint", mock_verify_and_mint)

    send_control_calls = []

    async def mock_send_control(ide_id, message):
        send_control_calls.append((ide_id, message))
        return True

    monkeypatch.setattr(cr.ide_registry, "send_control", mock_send_control)

    req = FakeRequest(
        body={"totp": "123456", "password": "pwd"},
        path_params={"session_id": "s1"}
    )
    resp = await cr.elevate_session(req)
    assert resp.status_code == 200

    data = _json.loads(resp.body)
    assert data["status"] == "ok"
    assert data["expires_at"] == 123.0
    assert data["ttl"] == 300

    assert len(send_control_calls) == 1
    assert send_control_calls[0][0] == "ide1"
    assert send_control_calls[0][1]["type"] == "elevation_granted"
    assert send_control_calls[0][1]["session_id"] == "s1"
    assert send_control_calls[0][1]["expires_at"] == 123.0


@pytest.mark.asyncio
async def test_elevate_locked(monkeypatch):
    monkeypatch.setattr(cr.session_registry, "get", lambda sid: FakeSession("s1", "alice", "ide1"))

    async def mock_verify_and_mint(session_id, subject, totp=None, password=None):
        return {"status": "error", "error": "locked", "locked_until": 999}

    monkeypatch.setattr(cr.elevation_service, "verify_and_mint", mock_verify_and_mint)

    kill_calls = []

    async def mock_kill_relay_session(subject, session_id):
        kill_calls.append((subject, session_id))
        return {"status": "ok"}

    monkeypatch.setattr(cr, "kill_relay_session", mock_kill_relay_session)

    req = FakeRequest(
        body={"totp": "123456"},
        path_params={"session_id": "s1"}
    )
    resp = await cr.elevate_session(req)
    assert resp.status_code == 403

    data = _json.loads(resp.body)
    assert data["status"] == "error"
    assert data["error"] == "locked"
    assert data["locked_until"] == 999

    assert len(kill_calls) == 1
    assert kill_calls[0] == ("alice", "s1")


@pytest.mark.asyncio
async def test_elevate_invalid_factor(monkeypatch):
    monkeypatch.setattr(cr.session_registry, "get", lambda sid: FakeSession("s1", "alice", "ide1"))

    async def mock_verify_and_mint(session_id, subject, totp=None, password=None):
        return {"status": "error", "error": "invalid_factor"}

    monkeypatch.setattr(cr.elevation_service, "verify_and_mint", mock_verify_and_mint)

    req = FakeRequest(
        body={"totp": "111111"},
        path_params={"session_id": "s1"}
    )
    resp = await cr.elevate_session(req)
    assert resp.status_code == 401

    data = _json.loads(resp.body)
    assert data["status"] == "error"
    assert data["error"] == "invalid_factor"


@pytest.mark.asyncio
async def test_elevate_not_found(monkeypatch):
    monkeypatch.setattr(cr.session_registry, "get", lambda sid: None)

    req = FakeRequest(
        body={"totp": "123456"},
        path_params={"session_id": "s1"}
    )
    resp = await cr.elevate_session(req)
    assert resp.status_code == 404

    data = _json.loads(resp.body)
    assert data["status"] == "error"
    assert data["error"] == "not_found"


@pytest.mark.asyncio
async def test_elevate_forbidden(monkeypatch):
    monkeypatch.setattr(cr.session_registry, "get", lambda sid: FakeSession("s1", "bob", "ide1"))

    req = FakeRequest(
        body={"totp": "123456"},
        path_params={"session_id": "s1"}
    )
    resp = await cr.elevate_session(req)
    assert resp.status_code == 403

    data = _json.loads(resp.body)
    assert data["status"] == "error"
    assert data["error"] == "forbidden"


@pytest.mark.asyncio
async def test_elevate_unauthenticated(monkeypatch):
    monkeypatch.delenv("CAT_TERMINAL_DEV_AUTH", raising=False)
    req = FakeRequest(path_params={"session_id": "s1"})
    resp = await cr.elevate_session(req)
    assert resp.status_code == 401
    data = _json.loads(resp.body)
    assert data["error"] == "unauthenticated"


class FakeVaultService:
    def __init__(self, exists_val=False, data=None):
        self.exists_val = exists_val
        self.data = data or {}
        self.set_calls = []
        self.delete_calls = []

    async def exists(self, plugin_id, key_name):
        if key_name in self.data:
            return True
        return self.exists_val

    async def get(self, plugin_id, key_name):
        return self.data.get(key_name)

    async def set(self, plugin_id, key_name, value):
        self.set_calls.append((plugin_id, key_name, value))
        self.data[key_name] = value

    async def delete(self, plugin_id, key_name):
        self.delete_calls.append((plugin_id, key_name))
        self.data.pop(key_name, None)


@pytest.mark.asyncio
async def test_provision_totp_fresh(monkeypatch):
    fake_vault = FakeVaultService(exists_val=False)
    monkeypatch.setattr(cr, "VaultService", lambda: fake_vault)

    req = FakeRequest()
    resp = await cr.provision_totp(req)
    assert resp.status_code == 200

    data = _json.loads(resp.body)
    assert data["status"] == "ok"
    assert data["otpauth_uri"].startswith("otpauth://")
    assert "alice" in data["otpauth_uri"]

    assert len(fake_vault.set_calls) == 1
    plugin_id, key_name, seed = fake_vault.set_calls[0]
    assert plugin_id == "cat_terminal_relay_plugin"
    assert key_name == "PENDING_TOTP_SEED"
    assert len(seed) > 0


@pytest.mark.asyncio
async def test_provision_totp_already(monkeypatch):
    fake_vault = FakeVaultService(exists_val=True)
    async def mock_exists(plugin_id, key_name):
        return key_name == "TOTP_SEED"
    fake_vault.exists = mock_exists
    monkeypatch.setattr(cr, "VaultService", lambda: fake_vault)

    req = FakeRequest()
    resp = await cr.provision_totp(req)
    assert resp.status_code == 409

    data = _json.loads(resp.body)
    assert data["status"] == "error"
    assert data["error"] == "already_provisioned"


@pytest.mark.asyncio
async def test_totp_status_unprovisioned(monkeypatch):
    fake_vault = FakeVaultService(exists_val=False)
    monkeypatch.setattr(cr, "VaultService", lambda: fake_vault)

    req = FakeRequest()
    resp = await cr.totp_status(req)
    assert resp.status_code == 200
    data = _json.loads(resp.body)
    assert data["status"] == "ok"
    assert data["totp_provisioned"] is False
    assert data["password_provisioned"] is False


@pytest.mark.asyncio
async def test_totp_status_provisioned(monkeypatch):
    fake_vault = FakeVaultService(data={"TOTP_SEED": "myseed", "PASSWORD_HASH": "myhash"})
    monkeypatch.setattr(cr, "VaultService", lambda: fake_vault)

    req = FakeRequest()
    resp = await cr.totp_status(req)
    assert resp.status_code == 200
    data = _json.loads(resp.body)
    assert data["status"] == "ok"
    assert data["totp_provisioned"] is True
    assert data["password_provisioned"] is True


@pytest.mark.asyncio
async def test_verify_totp_ok(monkeypatch):
    import pyotp
    seed = pyotp.random_base32()
    fake_vault = FakeVaultService(data={"PENDING_TOTP_SEED": seed})
    monkeypatch.setattr(cr, "VaultService", lambda: fake_vault)

    code = pyotp.TOTP(seed).now()
    req = FakeRequest(body={"code": code})
    resp = await cr.verify_totp(req)
    assert resp.status_code == 200
    data = _json.loads(resp.body)
    assert data["status"] == "ok"

    assert "TOTP_SEED" in fake_vault.data
    assert "PENDING_TOTP_SEED" not in fake_vault.data


@pytest.mark.asyncio
async def test_verify_totp_invalid_code(monkeypatch):
    fake_vault = FakeVaultService(data={"PENDING_TOTP_SEED": "somependingseed"})
    monkeypatch.setattr(cr, "VaultService", lambda: fake_vault)

    req = FakeRequest(body={"code": "000000"})
    resp = await cr.verify_totp(req)
    assert resp.status_code == 400
    data = _json.loads(resp.body)
    assert data["status"] == "error"
    assert data["error"] == "invalid_code"


@pytest.mark.asyncio
async def test_verify_totp_no_pending(monkeypatch):
    fake_vault = FakeVaultService(exists_val=False)
    monkeypatch.setattr(cr, "VaultService", lambda: fake_vault)

    req = FakeRequest(body={"code": "123456"})
    resp = await cr.verify_totp(req)
    assert resp.status_code == 400
    data = _json.loads(resp.body)
    assert data["status"] == "error"
    assert data["error"] == "not_initiated"


@pytest.mark.asyncio
async def test_provision_password_ok(monkeypatch):
    fake_vault = FakeVaultService()
    monkeypatch.setattr(cr, "VaultService", lambda: fake_vault)

    req = FakeRequest(body={"password": "hunter2"})
    resp = await cr.provision_password(req)
    assert resp.status_code == 200

    data = _json.loads(resp.body)
    assert data["status"] == "ok"

    assert len(fake_vault.set_calls) == 1
    plugin_id, key_name, hashed = fake_vault.set_calls[0]
    assert plugin_id == "cat_terminal_relay_plugin"
    assert key_name == "PASSWORD_HASH"
    assert hashed != "hunter2"  # should be hashed by PasswordHasher


@pytest.mark.asyncio
async def test_provision_password_missing(monkeypatch):
    fake_vault = FakeVaultService()
    monkeypatch.setattr(cr, "VaultService", lambda: fake_vault)

    # Missing field
    req = FakeRequest(body={})
    resp = await cr.provision_password(req)
    assert resp.status_code == 400

    data = _json.loads(resp.body)
    assert data["status"] == "error"
    assert data["error"] == "missing_required_fields"
    assert data["missing_fields"] == ["password"]

    # Empty/whitespace password
    req = FakeRequest(body={"password": "   "})
    resp = await cr.provision_password(req)
    assert resp.status_code == 400
