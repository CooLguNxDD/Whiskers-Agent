"""Unit tests for headless CLI agent drivers and runner."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import base64
from core_graph.goap_agent.cli.base import CliRunOptions, materialize_root_private_binary
from core_graph.goap_agent.cli.claude import ClaudeCliDriver
from core_graph.goap_agent.cli.agy import AgyCliDriver
from core_graph.goap_agent.cli.generic import GenericCliDriver
from core_graph.goap_agent.cli.grok import GrokCliDriver
from core_graph.goap_agent.cli.registry import get_driver, list_drivers
from core_graph.goap_agent.cli.runner import run_cli_agent, reset_semaphore_for_tests


@pytest.fixture(autouse=True)
def _reset_sem():
    reset_semaphore_for_tests()
    yield
    reset_semaphore_for_tests()


def test_list_drivers():
    names = list_drivers()
    assert "claude" in names
    assert "agy" in names
    assert "grok" in names
    assert "generic" in names


def test_get_driver_aliases():
    assert get_driver("claude-cli").name == "claude"
    assert get_driver("agy-cli").name == "agy"
    assert get_driver("antigravity").name == "agy"
    assert get_driver("grok-cli").name == "grok"


def test_claude_build_cmd_oauth_skips_bare(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok_test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CLI_BARE", raising=False)
    d = ClaudeCliDriver()
    cmd = d.build_cmd("hello", options=CliRunOptions(model="sonnet"))
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "hello" in cmd
    assert "--bare" not in cmd  # bare would skip OAuth token
    assert "--output-format" in cmd
    assert "json" in cmd
    assert "--model" in cmd
    assert "sonnet" in cmd
    # With run_as_user unset, root parent omits the flag; non-root includes it.
    # (Privilege drop to whiskers-claude enables the flag — covered in test_cli_user_drop.)
    import os
    from unittest.mock import patch
    with patch("core_graph.goap_agent.cli.user_drop.resolve_cli_run_user", return_value=None):
        cmd2 = d.build_cmd("hello", options=CliRunOptions(model="sonnet", run_as_user=""))
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        assert "--dangerously-skip-permissions" not in cmd2
    else:
        assert "--dangerously-skip-permissions" in cmd2


def test_claude_build_cmd_pool_oauth_env_skips_bare(monkeypatch):
    """Frontend pool OAuth override lives in options.env, not process env."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-parent-should-lose")
    monkeypatch.delenv("CLAUDE_CLI_BARE", raising=False)
    d = ClaudeCliDriver()
    opts = CliRunOptions(
        env={"CLAUDE_CODE_OAUTH_TOKEN": "pool-oauth-token"},
    )
    cmd = d.build_cmd("hello", options=opts)
    assert "--bare" not in cmd


def test_claude_build_cmd_api_key_uses_bare(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CLAUDE_CLI_BARE", raising=False)
    d = ClaudeCliDriver()
    cmd = d.build_cmd("hello", options=CliRunOptions())
    assert "--bare" in cmd


def test_claude_parse_json_result():
    d = ClaudeCliDriver()
    payload = '{"result":"done","session_id":"abc","total_cost_usd":0.01}'
    r = d.parse_result(payload, "", 0)
    assert r.status == "ok"
    assert r.text == "done"
    assert r.meta.get("session_id") == "abc"


def test_claude_parse_event_array_model_not_found():
    """Verbose/newer Claude Code may emit a JSON array of stream events."""
    d = ClaudeCliDriver()
    payload = (
        '[{"type":"system","subtype":"init","model":"claude-cli"},'
        '{"type":"assistant","message":{"content":[{"type":"text",'
        '"text":"There\'s an issue with the selected model (claude-cli)."}]}},'
        '{"type":"result","subtype":"success","is_error":true,"result":'
        '"There\'s an issue with the selected model (claude-cli). It may not exist '
        'or you may not have access to it. Run --model to pick a different model.",'
        '"session_id":"abc"}]'
    )
    r = d.parse_result(payload, "", 1)
    assert r.status == "error"
    assert "claude-cli" in (r.text or "")
    assert "claude-cli" in (r.error or "")
    assert r.meta.get("is_error") is True


def test_claude_build_cmd_omits_model_when_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    d = ClaudeCliDriver()
    cmd = d.build_cmd("hello", options=CliRunOptions(model=None))
    assert "--model" not in cmd


def test_agy_build_cmd_headless():
    d = AgyCliDriver()
    cmd = d.build_cmd("task", options=CliRunOptions())
    assert cmd[0] == "agy"
    assert "-p" in cmd
    assert "task" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--headless" not in cmd


def test_grok_build_cmd_always_approve():
    d = GrokCliDriver()
    cmd = d.build_cmd("task", options=CliRunOptions(model="grok-4.5"))
    assert cmd[0] == "grok"
    assert "-p" in cmd
    assert "--always-approve" in cmd
    assert "--output-format" in cmd
    assert "--model" in cmd
    assert "grok-4.5" in cmd


def test_generic_template_mcp_placeholders(monkeypatch):
    monkeypatch.setenv(
        "CLI_AGENT_CMD",
        "myagent --mcp {mcp_config} --rules {instructions_file} {prompt}",
    )
    d = GenericCliDriver()
    cmd = d.build_cmd(
        "hi",
        options=CliRunOptions(
            workdir="/tmp",
            mcp_config_path="/tmp/mcp.json",
            append_system_prompt_file="/tmp/instr.md",
        ),
    )
    assert cmd[0] == "myagent"
    assert "/tmp/mcp.json" in cmd
    assert "/tmp/instr.md" in cmd
    assert "hi" in cmd


def test_generic_requires_cmd(monkeypatch):
    monkeypatch.delenv("CLI_AGENT_CMD", raising=False)
    d = GenericCliDriver()
    assert d.available() is False
    with pytest.raises(ValueError):
        d.build_cmd("x", options=CliRunOptions())


def test_generic_template(monkeypatch):
    monkeypatch.setenv("CLI_AGENT_CMD", "echo {prompt}")
    d = GenericCliDriver()
    cmd = d.build_cmd("hi there", options=CliRunOptions(workdir="/tmp"))
    assert cmd[0] == "echo"
    assert "hi there" in " ".join(cmd)


def test_generic_template_positional_braces_no_crash(monkeypatch):
    """Prompts/templates with {0} or shell braces must not raise IndexError."""
    monkeypatch.setenv("CLI_AGENT_CMD", "echo {prompt} {0} leftover{")
    d = GenericCliDriver()
    cmd = d.build_cmd("hello", options=CliRunOptions(workdir="/tmp"))
    assert cmd[0] == "echo"
    assert "hello" in cmd


def test_materialize_root_private_binary_noop_for_public_path(tmp_path, monkeypatch):
    """Binaries outside /root are returned unchanged (no copy)."""
    public = tmp_path / "grok"
    public.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    public.chmod(0o755)
    with patch(
        "core_graph.goap_agent.cli.base.which_binary",
        return_value=str(public),
    ):
        assert materialize_root_private_binary("grok") == str(public)


def test_materialize_root_private_binary_copies_from_root(tmp_path, monkeypatch):
    """Symlinks into /root are materialised so drop-user can exec them."""
    rootish = tmp_path / "root_home"
    rootish.mkdir()
    real = rootish / "grok-real"
    real.write_bytes(b"\x7fELFfake")
    real.chmod(0o755)
    link = tmp_path / "grok-link"
    link.symlink_to(real)

    dest_dir = tmp_path / "usr_local_bin"
    dest_dir.mkdir()
    dest = dest_dir / "grok-link"

    def _realpath(path: str) -> str:
        # Pretend the real path lives under /root so materialize triggers.
        if os.path.basename(path) == "grok-link" or path == str(link):
            return "/root/.grok/downloads/grok-linux-x86_64"
        return os.path.realpath(path)

    monkeypatch.setattr(
        "core_graph.goap_agent.cli.base.os.path.realpath",
        _realpath,
    )
    monkeypatch.setattr(
        "core_graph.goap_agent.cli.base.os.path.join",
        lambda *parts: str(dest) if parts[-1] == "grok-link" else os.path.join(*parts),
    )
    # Force "we are root" branch.
    monkeypatch.setattr(
        "core_graph.goap_agent.cli.base.os.geteuid",
        lambda: 0,
        raising=False,
    )

    copied = {}

    def _copy2(src, dst):
        # Source is the which_binary path (link); read via real file.
        data = real.read_bytes()
        with open(dst, "wb") as f:
            f.write(data)
        copied["dst"] = dst

    monkeypatch.setattr("core_graph.goap_agent.cli.base.shutil.copy2", _copy2)
    monkeypatch.setattr(
        "core_graph.goap_agent.cli.base.os.chmod",
        lambda path, mode: None,
    )
    monkeypatch.setattr(
        "core_graph.goap_agent.cli.base.os.replace",
        lambda src, dst: os.rename(src, dst) if src != dst else None,
    )

    with patch(
        "core_graph.goap_agent.cli.base.which_binary",
        return_value=str(link),
    ):
        out = materialize_root_private_binary("grok")

    assert out == str(dest)
    assert dest.exists()
    assert copied.get("dst")


@pytest.mark.asyncio
async def test_run_cli_agent_binary_not_found(monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_BINARY", "definitely-not-a-real-binary-xyz")
    result = await run_cli_agent("prompt", agent="claude")
    assert result.status == "binary_not_found"


@pytest.mark.asyncio
async def test_run_cli_agent_mocked_success(monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_BINARY", "claude")

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b'{"result":"ok-from-cli"}', b""

        def kill(self):
            pass

        async def wait(self):
            return 0

    with patch("core_graph.goap_agent.cli.runner.which_binary", return_value="/usr/bin/claude"):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.return_value = FakeProc()
            result = await run_cli_agent("hello", agent="claude")
    assert result.status == "ok"
    assert result.text == "ok-from-cli"
    assert result.agent == "claude"


@pytest.mark.asyncio
async def test_run_cli_agent_timeout(monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_BINARY", "claude")

    class HangProc:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(10)
            return b"", b""

        def kill(self):
            pass

        async def wait(self):
            return -9

    with patch("core_graph.goap_agent.cli.runner.which_binary", return_value="/usr/bin/claude"):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.return_value = HangProc()
            result = await run_cli_agent(
                "hello",
                agent="claude",
                options=CliRunOptions(timeout_s=0.05),
            )
    assert result.status == "timeout"


def test_grok_build_cmd(monkeypatch):
    monkeypatch.setenv("GROK_AUTH_JSON", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-zN3bV1I...")
    d = GrokCliDriver()
    d.ensure_grok_auth = MagicMock()
    cmd = d.build_cmd("hello", options=CliRunOptions(model="grok-beta"))
    assert cmd[0] == "grok"
    assert "-p" in cmd
    assert "hello" in cmd
    assert "--output-format" in cmd
    assert "json" in cmd
    assert "--model" in cmd
    assert "grok-beta" in cmd
    d.ensure_grok_auth.assert_called_once()


def test_grok_ensure_auth(tmp_path):
    d = GrokCliDriver()
    auth_data = '{"token": "xyz"}'
    auth_b64 = base64.b64encode(auth_data.encode()).decode()
    
    d.ensure_grok_auth(auth_b64, home=str(tmp_path))
    auth_file = tmp_path / ".grok" / "auth.json"
    assert auth_file.exists()
    assert auth_file.read_text() == auth_data


def test_grok_parse_result():
    d = GrokCliDriver()
    payload = '{"result":"done","session_id":"grok-abc"}'
    r = d.parse_result(payload, "", 0)
    assert r.status == "ok"
    assert r.text == "done"
    assert r.meta.get("session_id") == "grok-abc"

