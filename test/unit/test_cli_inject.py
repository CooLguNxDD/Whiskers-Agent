"""Unit tests for GoapAgent CLI instruction + MCP config injection."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core_graph.goap_agent.cli.base import CliRunOptions
from core_graph.goap_agent.cli.claude import ClaudeCliDriver
from core_graph.goap_agent.cli_inject import (
    DEFAULT_INSTRUCTIONS_PATH,
    build_mcp_config_dict,
    compose_prompt,
    load_cli_instructions,
    prepare_cli_injection,
    resolve_mcp_url,
    write_mcp_config_file,
)


def test_default_instructions_file_exists():
    assert DEFAULT_INSTRUCTIONS_PATH.is_file()
    text = load_cli_instructions()
    assert "GoapAgent" in text
    assert "turn_init" in text


def test_load_instructions_override(tmp_path, monkeypatch):
    p = tmp_path / "custom.md"
    p.write_text("# custom goap\nuse tools", encoding="utf-8")
    monkeypatch.setenv("GOAP_AGENT_INSTRUCTIONS_PATH", str(p))
    assert "custom goap" in load_cli_instructions()


def test_resolve_mcp_url_from_server(monkeypatch):
    monkeypatch.delenv("GOAP_AGENT_MCP_URL", raising=False)
    monkeypatch.delenv("CLI_AGENT_MCP_URL", raising=False)
    monkeypatch.setenv("MCP_SERVER_URL", "http://localhost:10000")
    assert resolve_mcp_url() == "http://127.0.0.1:10000/mcp"


def test_resolve_mcp_url_explicit(monkeypatch):
    monkeypatch.setenv("GOAP_AGENT_MCP_URL", "http://example.test/mcp")
    assert resolve_mcp_url() == "http://example.test/mcp"


def test_build_and_write_mcp_config(tmp_path):
    cfg = build_mcp_config_dict(url="http://127.0.0.1:10000/mcp", token="tok123")
    server_key = "whiskers-goap" if "whiskers-goap" in cfg["mcpServers"] else "whiskers-goap"
    assert cfg["mcpServers"][server_key]["type"] == "http"
    assert cfg["mcpServers"][server_key]["headers"]["Authorization"] == "Bearer tok123"
    path = write_mcp_config_file(cfg, directory=str(tmp_path))
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["mcpServers"][server_key]["url"].endswith("/mcp")


def test_claude_cmd_includes_mcp_and_prompt_file(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    d = ClaudeCliDriver()
    cmd = d.build_cmd(
        "do the thing",
        options=CliRunOptions(
            mcp_config_path="/tmp/mcp.json",
            strict_mcp_config=True,
            append_system_prompt_file="/tmp/instr.md",
        ),
    )
    assert "--mcp-config" in cmd
    assert "/tmp/mcp.json" in cmd
    assert "--strict-mcp-config" in cmd
    assert "--append-system-prompt-file" in cmd
    assert "/tmp/instr.md" in cmd
    assert "do the thing" in cmd


def test_compose_prompt_prepend():
    from core_graph.goap_agent.cli_inject import CliInjection

    inj = CliInjection(instructions="RULES")
    assert "RULES" in compose_prompt("goal", injection=inj, prepend_instructions=True)
    assert compose_prompt("goal", injection=inj, prepend_instructions=False) == "goal"


@pytest.mark.asyncio
async def test_prepare_injection_writes_files(monkeypatch, tmp_path):
    monkeypatch.setenv("GOAP_AGENT_AUTO_MCP", "1")
    monkeypatch.setenv("GOAP_AGENT_MCP_TOKEN", "static-bearer")
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:10000")
    monkeypatch.delenv("GOAP_AGENT_MCP_CONFIG", raising=False)

    inj = await prepare_cli_injection(session_id="sess-1", agent="claude")
    try:
        assert inj.instructions
        assert "sess-1" in inj.instructions
        assert inj.mcp_config_path and Path(inj.mcp_config_path).is_file()
        assert inj.instructions_path and Path(inj.instructions_path).is_file()
        assert inj.token_source == "env"
        data = json.loads(Path(inj.mcp_config_path).read_text(encoding="utf-8"))
        server_key = "whiskers-goap" if "whiskers-goap" in data["mcpServers"] else "whiskers-goap"
        headers = data["mcpServers"][server_key]["headers"]
        assert headers["Authorization"] == "Bearer static-bearer"
        assert inj.strict_mcp is True
    finally:
        inj.cleanup()
    assert not Path(inj.mcp_config_path).exists()


@pytest.mark.asyncio
async def test_prepare_injection_disabled(monkeypatch):
    monkeypatch.setenv("GOAP_AGENT_AUTO_MCP", "0")
    inj = await prepare_cli_injection(agent="claude", inject_mcp=False)
    try:
        assert inj.token_source == "disabled"
        assert inj.mcp_config_path is None
        # instructions file still materialised for claude
        assert inj.instructions
        assert inj.instructions_path
    finally:
        inj.cleanup()


@pytest.mark.asyncio
async def test_prepare_injection_writes_mcp_for_agy(monkeypatch):
    """All CLI drivers get auto-MCP wiring (not Claude-only)."""
    monkeypatch.setenv("GOAP_AGENT_AUTO_MCP", "1")
    monkeypatch.setenv("GOAP_AGENT_MCP_TOKEN", "agy-bearer")
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:10000")
    monkeypatch.delenv("GOAP_AGENT_MCP_CONFIG", raising=False)
    inj = await prepare_cli_injection(agent="agy")
    try:
        assert inj.token_source == "env"
        assert inj.mcp_config_path and Path(inj.mcp_config_path).is_file()
        assert inj.instructions_path and Path(inj.instructions_path).is_file()
        assert inj.strict_mcp is False  # Claude-only flag
        data = json.loads(Path(inj.mcp_config_path).read_text(encoding="utf-8"))
        server_key = "whiskers-goap" if "whiskers-goap" in data["mcpServers"] else "whiskers-goap"
        assert "Authorization" in data["mcpServers"][server_key]["headers"]
    finally:
        inj.cleanup()


@pytest.mark.asyncio
async def test_prepare_injection_writes_mcp_for_grok(monkeypatch):
    monkeypatch.setenv("GOAP_AGENT_AUTO_MCP", "1")
    monkeypatch.setenv("GOAP_AGENT_MCP_TOKEN", "grok-bearer")
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:10000")
    monkeypatch.delenv("GOAP_AGENT_MCP_CONFIG", raising=False)
    inj = await prepare_cli_injection(agent="grok")
    try:
        assert inj.token_source == "env"
        assert inj.mcp_config_path and Path(inj.mcp_config_path).is_file()
    finally:
        inj.cleanup()


@pytest.mark.asyncio
async def test_run_cli_agent_dict_passes_mcp(monkeypatch):
    monkeypatch.setenv("GOAP_AGENT_AUTO_MCP", "1")
    monkeypatch.setenv("GOAP_AGENT_MCP_TOKEN", "tok")
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:10000")
    monkeypatch.setenv("CLAUDE_CLI_BINARY", "claude")

    captured: dict = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b'{"result":"ok"}', b""

        def kill(self):
            pass

        async def wait(self):
            return 0

    async def fake_spawn(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return FakeProc()

    from core_graph.goap_agent.cli.runner import run_cli_agent_dict, reset_semaphore_for_tests

    reset_semaphore_for_tests()
    with patch("core_graph.goap_agent.cli.runner.which_binary", return_value="/usr/bin/claude"):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.side_effect = fake_spawn
            out = await run_cli_agent_dict("fetch the fish game", agent="claude")
    assert out["status"] == "ok"
    assert out["inject"]["mcp_config"] is True
    cmd = captured["cmd"]
    assert "--mcp-config" in cmd
    assert "--append-system-prompt-file" in cmd
    # User goal only in -p when instructions are file-appended
    p_idx = cmd.index("-p")
    assert "fetch the fish game" in cmd[p_idx + 1]


@pytest.mark.asyncio
async def test_run_cli_agent_dict_places_mcp_for_grok(monkeypatch, tmp_path):
    """Grok Mode B gets workdir .grok/config.toml with Whiskers Agent server."""
    monkeypatch.setenv("GOAP_AGENT_AUTO_MCP", "1")
    monkeypatch.setenv("GOAP_AGENT_MCP_TOKEN", "tok")
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:10000")
    monkeypatch.setenv("GROK_CLI_BINARY", "grok")
    monkeypatch.setenv("CLI_AGENT_DROP_PRIVS", "0")
    monkeypatch.delenv("CLI_AGENT_USER", raising=False)
    monkeypatch.delenv("GOAP_AGENT_CLI_USER", raising=False)

    captured: dict = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b'{"result":"ok"}', b""

        def kill(self):
            pass

        async def wait(self):
            return 0

    async def fake_spawn(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["cwd"] = kwargs.get("cwd")
        # Discovery file must exist at spawn time
        grok_cfg = Path(kwargs.get("cwd") or tmp_path) / ".grok" / "config.toml"
        captured["grok_toml"] = grok_cfg.read_text(encoding="utf-8") if grok_cfg.is_file() else None
        return FakeProc()

    from core_graph.goap_agent.cli.runner import run_cli_agent_dict, reset_semaphore_for_tests

    reset_semaphore_for_tests()
    with patch("core_graph.goap_agent.cli.runner.which_binary", return_value="/usr/local/bin/grok"):
        with patch(
            "core_graph.goap_agent.cli.runner.materialize_root_private_binary",
            return_value="/usr/local/bin/grok",
        ):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
                spawn.side_effect = fake_spawn
                out = await run_cli_agent_dict(
                    "drive goap",
                    agent="grok",
                    workdir=str(tmp_path),
                )
    assert out["status"] == "ok"
    assert out["inject"]["mcp_config"] is True
    assert out["inject"]["token_source"] == "env"
    assert captured.get("grok_toml")
    assert "whiskers-goap" in captured["grok_toml"] or "whiskers-goap" in captured["grok_toml"]
    assert "127.0.0.1:10000/mcp" in captured["grok_toml"]
    assert "Bearer tok" in captured["grok_toml"]
    assert "--always-approve" in captured["cmd"]
    # Ephemeral discovery restored after run
    assert not (tmp_path / ".grok" / "config.toml").exists()


@pytest.mark.asyncio
async def test_run_cli_agent_dict_places_mcp_for_agy(monkeypatch, tmp_path):
    monkeypatch.setenv("GOAP_AGENT_AUTO_MCP", "1")
    monkeypatch.setenv("GOAP_AGENT_MCP_TOKEN", "tok")
    monkeypatch.setenv("MCP_SERVER_URL", "http://127.0.0.1:10000")
    monkeypatch.setenv("AGY_CLI_BINARY", "agy")
    monkeypatch.setenv("CLI_AGENT_DROP_PRIVS", "0")
    monkeypatch.delenv("CLI_AGENT_USER", raising=False)
    monkeypatch.delenv("GOAP_AGENT_CLI_USER", raising=False)

    captured: dict = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b'{"result":"ok"}', b""

        def kill(self):
            pass

        async def wait(self):
            return 0

    async def fake_spawn(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        cfg = Path(kwargs.get("cwd") or tmp_path) / ".agents" / "mcp_config.json"
        captured["agy_cfg"] = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else None
        return FakeProc()

    from core_graph.goap_agent.cli.runner import run_cli_agent_dict, reset_semaphore_for_tests

    reset_semaphore_for_tests()
    with patch("core_graph.goap_agent.cli.runner.which_binary", return_value="/usr/local/bin/agy"):
        with patch(
            "core_graph.goap_agent.cli.runner.materialize_root_private_binary",
            return_value="/usr/local/bin/agy",
        ):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
                spawn.side_effect = fake_spawn
                out = await run_cli_agent_dict(
                    "drive goap",
                    agent="agy",
                    workdir=str(tmp_path),
                )
    assert out["status"] == "ok"
    assert out["inject"]["mcp_config"] is True
    assert captured.get("agy_cfg")
    entry = captured["agy_cfg"]["mcpServers"].get("whiskers-goap") or captured["agy_cfg"]["mcpServers"].get("whiskers-goap")
    assert entry is not None
    assert entry["serverUrl"].endswith("/mcp")
    assert entry["headers"]["Authorization"] == "Bearer tok"
    assert "--dangerously-skip-permissions" in captured["cmd"]
    assert not (tmp_path / ".agents" / "mcp_config.json").exists()


@pytest.mark.asyncio
async def test_run_cli_agent_dict_passes_env_to_subprocess(monkeypatch):
    """One-shot callers (core_graph.goap_agent.oneshot) pass pool auth env
    (e.g. CLAUDE_CODE_OAUTH_TOKEN) via run_cli_agent_dict's env param — it must
    reach the actual spawned subprocess env, same as Mode A's CliAgentChatModel."""
    monkeypatch.setenv("GOAP_AGENT_AUTO_MCP", "0")  # keep this test focused on env merge
    monkeypatch.setenv("CLAUDE_CLI_BINARY", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-process-key")

    captured: dict = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b'{"result":"ok"}', b""

        def kill(self):
            pass

        async def wait(self):
            return 0

    async def fake_spawn(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env")
        return FakeProc()

    from core_graph.goap_agent.cli.runner import run_cli_agent_dict, reset_semaphore_for_tests

    reset_semaphore_for_tests()
    with patch("core_graph.goap_agent.cli.runner.which_binary", return_value="/usr/bin/claude"):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.side_effect = fake_spawn
            out = await run_cli_agent_dict(
                "one-shot goal",
                agent="claude",
                env={"CLAUDE_CODE_OAUTH_TOKEN": "pool-oauth-token", "ANTHROPIC_API_KEY": ""},
            )
    assert out["status"] == "ok"
    env = captured["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "pool-oauth-token"
    # Empty-string override pops the inherited parent key (see runner.py env merge).
    assert "ANTHROPIC_API_KEY" not in env
