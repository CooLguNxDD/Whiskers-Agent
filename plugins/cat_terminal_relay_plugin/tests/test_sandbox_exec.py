import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from plugins.cat_terminal_relay_plugin import command_guard
from plugins.cat_terminal_relay_plugin.command_guard import guard_pipeline
from plugins.cat_terminal_relay_plugin.MCPTools import sandbox_tools
from plugins.cat_terminal_relay_plugin.MCPTools.sandbox_tools import exec_command
import os
from plugins.cat_terminal_relay_plugin.command_guard import classify_privileged
from plugins.cat_terminal_relay_plugin.config_loader import load_env_defaults



class TestGuardPipeline:
    def test_allows_chained_allowlisted(self):
        result = guard_pipeline("git status | grep foo", allowed=frozenset({"git", "grep"}))
        assert result["allowed"] is True
        assert result["privileged"] is False
        assert "git" in result["binaries"]
        assert "grep" in result["binaries"]

    def test_rejects_unlisted_segment_binary(self):
        result = guard_pipeline("git status | rm -rf x", allowed=frozenset({"git"}))
        assert result["allowed"] is False
        assert result["reason"] == "binary_not_allowed"

    def test_blocks_dollar_substitution(self):
        result = guard_pipeline("echo $(whoami)", allowed=frozenset({"echo", "whoami"}))
        assert result["allowed"] is False
        assert result["reason"].startswith("blocked_pattern")

    def test_blocks_backtick(self):
        result = guard_pipeline("echo `whoami`", allowed=frozenset({"echo"}))
        assert result["allowed"] is False
        assert result["reason"].startswith("blocked_pattern")

    def test_flags_privileged_claude(self):
        result = guard_pipeline("claude --version", allowed=frozenset({"claude"}))
        assert result["allowed"] is True
        assert result["privileged"] is True

    def test_flags_privileged_agy_p(self):
        result = guard_pipeline("agy -p do-thing", allowed=frozenset({"agy"}))
        assert result["privileged"] is True

    def test_parse_error_on_bad_quoting(self):
        result = guard_pipeline("echo 'unterminated", allowed=frozenset({"echo"}))
        assert result["allowed"] is False
        assert result["reason"].startswith("parse_error")

    def test_empty_line(self):
        result = guard_pipeline("", allowed=frozenset({"echo"}))
        assert result["allowed"] is True
        assert result["binaries"] == []

    def test_allows_and_chaining(self):
        result = guard_pipeline("ls && echo hi", allowed=frozenset({"ls", "echo"}))
        assert result["allowed"] is True

    def test_redirect_target_not_treated_as_binary(self):
        result = guard_pipeline("ls > out.txt", allowed=frozenset({"ls"}))
        assert result["allowed"] is True

    def test_deny_all_default(self):
        result = guard_pipeline("ls", allowed=frozenset())
        assert result["allowed"] is False
        assert result["reason"] == "binary_not_allowed"

    def test_guard_pipeline_blocks_newline_injection(self):
        v = command_guard.guard_pipeline("ls\nrm -rf /", allowed=frozenset({"ls"}))
        assert v["allowed"] is False
        assert v["reason"] == "blocked_pattern:newline"

    def test_guard_pipeline_rejects_empty_segment_after_redirect_strip(self):
        v = command_guard.guard_pipeline("ls | > /tmp/pwned", allowed=frozenset({"ls"}))
        assert v["allowed"] is False
        assert v["reason"] == "redirect_without_binary"

    def test_guard_pipeline_skips_leading_env_assignment(self):
        v = command_guard.guard_pipeline("VAR=1 ls", allowed=frozenset({"ls"}))
        assert v["allowed"] is True
        assert v["binaries"] == ["ls"]

    def test_guard_pipeline_rejects_long_command(self):
        long_line = "echo " + "a" * 4096
        v = command_guard.guard_pipeline(long_line, allowed=frozenset({"echo"}))
        assert v["allowed"] is False
        assert v["reason"] == "command_too_long"


class _FakeStream:
    """Minimal StreamReader stand-in: one .read() returns the payload, then EOF."""
    def __init__(self, data: bytes):
        self._data = data
        self._sent = False

    async def read(self, n=-1):
        if self._sent:
            return b""
        self._sent = True
        return self._data


def _fake_proc(out=b"hi\n", err=b"", code=0):
    proc = MagicMock()
    proc.stdout = _FakeStream(out)
    proc.stderr = _FakeStream(err)
    proc.wait = AsyncMock(return_value=code)
    proc.returncode = code
    proc.pid = 4242
    proc.kill = MagicMock()
    return proc


class TestExecCommand:
    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"echo"}))
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=True))
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_happy_path(self, mock_shell):
        mock_shell.return_value = _fake_proc(b"hi\n", b"", 0)
        res = await exec_command("echo hi")
        assert res["status"] == "ok"
        assert res["exit_code"] == 0
        assert "hi" in res["stdout"]
        assert res["truncated"] is False

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=(None, set())))
    async def test_exec_unauthorized(self):
        res = await exec_command("echo hi")
        assert res["status"] == "error"
        assert res["error"] == "unauthorized"

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", set())))
    async def test_exec_forbidden_scope(self):
        res = await exec_command("echo hi")
        assert res["error"] == "forbidden"

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"core:terminal:write"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"echo"}))
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=True))
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_migrated_scope_still_reaches_exec_command(self, mock_shell):
        """core_047_scope_cutover rewrites terminal:use -> core:terminal:write;
        a key holding only the migrated token must still reach exec_command
        via caller_has_scope's legacy-alias widening."""
        mock_shell.return_value = _fake_proc(b"hi\n", b"", 0)
        res = await exec_command("echo hi")
        assert res["status"] == "ok"

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset())
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_binary_not_allowed(self, mock_shell):
        res = await exec_command("rm -rf x")
        assert res["error"] == "binary_not_allowed"
        mock_shell.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"claude"}))
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=False))
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_privileged_requires_elevation(self, mock_shell):
        res = await exec_command("claude --version")
        assert res["error"] == "elevation_required"
        mock_shell.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"claude"}))
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=True))
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_privileged_with_elevation_runs(self, mock_shell):
        mock_shell.return_value = _fake_proc(b"v1\n", b"", 0)
        res = await exec_command("claude --version")
        assert res["status"] == "ok"

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"echo"}))
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=True))
    @patch("asyncio.create_subprocess_shell")
    @patch("asyncio.wait_for", AsyncMock(side_effect=asyncio.TimeoutError))
    @patch.object(sandbox_tools, "_kill_process_group", new_callable=AsyncMock)
    async def test_exec_timeout(self, mock_kill, mock_shell):
        proc = _fake_proc()
        proc.returncode = None  # still running when the timeout fires
        mock_shell.return_value = proc
        res = await exec_command("echo hi")
        assert res["error"] == "timeout"
        assert mock_kill.await_count >= 1

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"echo"}))
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=True))
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_scrubs_env(self, mock_shell):
        mock_shell.return_value = _fake_proc(b"hi\n", b"", 0)
        with patch.dict(os.environ, {"MASTER_KEY": "super-secret"}):
            await exec_command("echo hi")
        _, kwargs = mock_shell.call_args
        assert "MASTER_KEY" not in kwargs["env"]
        assert kwargs.get("start_new_session") is True

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"echo"}))
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=True))
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_output_capped_without_full_buffering(self, mock_shell):
        """A high-volume producer must be capped, not buffered in full before truncation."""
        big = b"x" * (10 * 1024 * 1024)
        with patch.object(sandbox_tools, "SANDBOX_MAX_OUTPUT_BYTES", 1024):
            mock_shell.return_value = _fake_proc(big, b"", 0)
            res = await exec_command("echo hi")
        assert res["truncated"] is True
        assert len(res["stdout"]) <= 1024

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"echo"}))
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=True))
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_invalid_workdir(self, mock_shell):
        res = await exec_command("echo hi", workdir="../escape")
        assert res["error"] == "invalid_workdir"
        mock_shell.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"echo"}))
    @patch.object(sandbox_tools, "SANDBOX_MAX_OUTPUT_BYTES", 4)
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=True))
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_truncation(self, mock_shell):
        mock_shell.return_value = _fake_proc(b"abcdefgh", b"", 0)
        res = await exec_command("echo x")
        assert res["truncated"] is True
        assert len(res["stdout"]) <= 4

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"echo"}))
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=False))
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_nonprivileged_requires_elevation_by_default(self, mock_shell):
        res = await exec_command("echo hi")
        assert res["error"] == "elevation_required"
        mock_shell.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(sandbox_tools, "_caller_identity", AsyncMock(return_value=("u1", {"terminal:use"})))
    @patch.object(command_guard, "SANDBOX_ALLOWED_BINARIES", frozenset({"echo"}))
    @patch.object(sandbox_tools.elevation_service, "is_elevated", MagicMock(return_value=False))
    @patch.object(sandbox_tools, "SETTINGS", {**sandbox_tools.SETTINGS, "sandbox_require_elevation": False})
    @patch("asyncio.create_subprocess_shell")
    async def test_exec_nonprivileged_allowed_when_setting_disabled(self, mock_shell):
        mock_shell.return_value = _fake_proc(b"hi\n", b"", 0)
        res = await exec_command("echo hi")
        assert res["status"] == "ok"


import json
from pathlib import Path
from unittest.mock import patch, AsyncMock
from plugins.cat_terminal_relay_plugin.routes import control_routes
from plugins.cat_terminal_relay_plugin.routes.control_routes import sandbox_elevate

class _FakeReq:
    def __init__(self, body):
        self._body = body
        self.cookies = {"session": "x"}
    async def json(self):
        return self._body


class TestSandboxElevate:
    @pytest.mark.asyncio
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes._session_subject", new_callable=AsyncMock)
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes.elevation_service.verify_and_mint", new_callable=AsyncMock)
    async def test_sandbox_elevate_ok(self, mock_verify, mock_sub):
        mock_sub.return_value = "u1"
        mock_verify.return_value = {"status": "ok", "token": "t", "expires_at": 123.0, "ttl": 300}
        resp = await sandbox_elevate(_FakeReq({"totp": "000000"}))
        assert resp.status_code == 200
        data = json.loads(resp.body.decode())
        assert data["status"] == "ok"
        assert data["expires_at"] == 123.0
        assert data["ttl"] == 300

    @pytest.mark.asyncio
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes._session_subject", new_callable=AsyncMock)
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes.elevation_service.verify_and_mint", new_callable=AsyncMock)
    async def test_sandbox_elevate_locked(self, mock_verify, mock_sub):
        mock_sub.return_value = "u1"
        mock_verify.return_value = {"status": "error", "error": "locked", "message": "x"}
        resp = await sandbox_elevate(_FakeReq({"totp": "000000"}))
        assert resp.status_code == 403
        data = json.loads(resp.body.decode())
        assert data["error"] == "locked"

    @pytest.mark.asyncio
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes._session_subject", new_callable=AsyncMock)
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes.elevation_service.verify_and_mint", new_callable=AsyncMock)
    async def test_sandbox_elevate_invalid(self, mock_verify, mock_sub):
        mock_sub.return_value = "u1"
        mock_verify.return_value = {"status": "error", "error": "invalid_factor", "message": "x"}
        resp = await sandbox_elevate(_FakeReq({"totp": "000000"}))
        assert resp.status_code == 401
        data = json.loads(resp.body.decode())
        assert data["error"] == "invalid_factor"

    @pytest.mark.asyncio
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes._session_subject", new_callable=AsyncMock)
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes.elevation_service.verify_and_mint", new_callable=AsyncMock)
    async def test_sandbox_elevate_unauthenticated(self, mock_verify, mock_sub):
        mock_sub.return_value = None
        resp = await sandbox_elevate(_FakeReq({}))
        assert resp.status_code == 401
        data = json.loads(resp.body.decode())
        assert data["error"] == "unauthenticated"

    @pytest.mark.asyncio
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes._session_subject", new_callable=AsyncMock)
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes.elevation_service.verify_and_mint", new_callable=AsyncMock)
    async def test_sandbox_elevate_via_api_key(self, mock_verify, mock_sub):
        mock_sub.return_value = None  # no session cookie
        mock_verify.return_value = {"status": "ok", "token": "t", "expires_at": 123.0, "ttl": 300}

        class _ApiKeyReq:
            cookies = {}
            headers = {"authorization": "Bearer octk_good"}
            async def json(self):
                return {}

        async def mock_lookup(token):
            return {"key_id": "ak_1", "subject": "svc", "scopes": ["sandbox:exec"]}

        with patch("core.api_key_management.store.lookup_active_by_token", mock_lookup):
            resp = await sandbox_elevate(_ApiKeyReq())
        assert resp.status_code == 200
        data = json.loads(resp.body.decode())
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes._session_subject", new_callable=AsyncMock)
    async def test_sandbox_elevate_api_key_missing_scope(self, mock_sub):
        """A scope from an unrelated domain must still be denied.

        Was ``["terminal:use"]`` before the boundary refactor routed this
        check through ``evaluate_access`` (repo-polish phase 3): that used
        a raw set-intersection with no grammar implication, so
        ``terminal:use`` (-> ``core:terminal:write``) happened to also miss
        the sandbox check by accident. Under ``evaluate_access``'s
        cross-domain hierarchy ``core:terminal:write`` *correctly* covers
        ``core:terminal.sandbox:write`` (same rule ``auth.py``'s
        ``test_write_implies_read`` already exercises for the WS handshake),
        so that scope now legitimately passes here too — see
        ``test_sandbox_elevate_via_api_key`` above, which asserts exactly
        that. This test now exercises a genuinely unrelated domain instead.
        """
        mock_sub.return_value = None

        class _ApiKeyReq:
            cookies = {}
            headers = {"authorization": "Bearer octk_good"}
            async def json(self):
                return {}

        async def mock_lookup(token):
            return {"key_id": "ak_1", "subject": "svc", "scopes": ["core:graph:read"]}

        with patch("core.api_key_management.store.lookup_active_by_token", mock_lookup):
            resp = await sandbox_elevate(_ApiKeyReq())
        assert resp.status_code == 401
        data = json.loads(resp.body.decode())
        assert data["error"] == "unauthenticated"

    @pytest.mark.asyncio
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes._session_subject", new_callable=AsyncMock)
    @patch("plugins.cat_terminal_relay_plugin.routes.control_routes.elevation_service.verify_and_mint", new_callable=AsyncMock)
    async def test_manifest_has_sandbox_settings(self, mock_verify, mock_sub):
        manifest_path = Path(control_routes.__file__).resolve().parents[1] / "manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.loads(f.read())
        settings = manifest.get("settings", {})
        assert "sandbox_timeout_s" in settings
        assert "sandbox_max_output_bytes" in settings
        assert "sandbox_workdir" in settings
        assert "sandbox_require_elevation" in settings


class TestConfigLoader:
    def test_injects_when_unset(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"_comment":"x",
            "sandbox":{"CAT_TEST_SANDBOX_KEY":"abc"}}), encoding="utf-8")
        monkeypatch.delenv("CAT_TEST_SANDBOX_KEY", raising=False)
        applied = load_env_defaults(path=cfg)
        assert os.environ.get("CAT_TEST_SANDBOX_KEY") == "abc"
        assert "CAT_TEST_SANDBOX_KEY" in applied

    def test_host_env_wins(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"sandbox":{"CAT_TEST_SANDBOX_KEY":"fromconfig"}}), encoding="utf-8")
        monkeypatch.setenv("CAT_TEST_SANDBOX_KEY","host")
        applied = load_env_defaults(path=cfg)
        assert os.environ["CAT_TEST_SANDBOX_KEY"] == "host"
        assert "CAT_TEST_SANDBOX_KEY" not in applied

    def test_empty_value_skipped(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"sandbox":{"CAT_TEST_EMPTY_KEY":""}}), encoding="utf-8")
        monkeypatch.delenv("CAT_TEST_EMPTY_KEY", raising=False)
        applied = load_env_defaults(path=cfg)
        assert "CAT_TEST_EMPTY_KEY" not in applied
        assert "CAT_TEST_EMPTY_KEY" not in os.environ

    def test_skips_underscore_sections(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"_comment":{"CAT_TEST_META":"nope"},
                    "common":{"CAT_TEST_REAL":"yes"}}), encoding="utf-8")
        monkeypatch.delenv("CAT_TEST_META", raising=False)
        monkeypatch.delenv("CAT_TEST_REAL", raising=False)
        applied = load_env_defaults(path=cfg)
        assert "CAT_TEST_META" not in applied
        assert applied.get("CAT_TEST_REAL") == "yes"

    def test_missing_file_returns_empty(self, tmp_path):
        applied = load_env_defaults(path=tmp_path / "does_not_exist.json")
        assert applied == {}

    def test_bad_json_returns_empty(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text("{not valid", encoding="utf-8")
        applied = load_env_defaults(path=cfg)
        assert applied == {}

    def test_privileged_default_is_claude_codex(self, monkeypatch):
        import importlib
        monkeypatch.delenv("CAT_PRIVILEGED_BINARIES", raising=False)
        importlib.reload(command_guard)
        assert command_guard.PRIVILEGED_BINARIES == frozenset({"claude", "codex"})

    def test_privileged_override(self):
        with patch.object(command_guard, "PRIVILEGED_BINARIES", frozenset({"gemini"})):
            assert classify_privileged("gemini do-thing") is True
            assert classify_privileged("claude do-thing") is False


