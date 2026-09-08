"""Unit tests for GoapAgent headless CLI run logging."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core_graph.goap_agent.cli.base import CliRunOptions
from core_graph.goap_agent.cli.claude import ClaudeCliDriver
from core_graph.goap_agent.cli.runner import run_cli_agent, reset_semaphore_for_tests
from core_graph.goap_agent.cli_run_log import (
    redact,
    sanitize_cmd,
    start_run_log,
)


@pytest.fixture(autouse=True)
def _reset_sem():
    reset_semaphore_for_tests()
    yield
    reset_semaphore_for_tests()


def test_redact_bearer():
    assert "***" in redact("Authorization: Bearer super-secret-token")
    assert "super-secret-token" not in redact("Authorization: Bearer super-secret-token")


def test_sanitize_cmd_truncates_prompt():
    long = "x" * 500
    cmd = ["claude", "-p", long, "--verbose"]
    safe = sanitize_cmd(cmd, prompt_max=40)
    assert safe[0] == "claude"
    assert "-p" in safe
    assert any("…" in a or len(a) <= 60 for a in safe)
    assert not any(a == long for a in safe)


def test_start_run_log_writes_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("GOAP_AGENT_LOG", "1")
    monkeypatch.setenv("GOAP_AGENT_LOG_DIR", str(tmp_path))
    log = start_run_log(agent="claude")
    log.line("hello internal")
    log.log_stream("stderr", "tool call foo")
    log.event("custom", n=1)
    info = log.finish(status="ok", returncode=0, text_preview="done")
    assert info["log_dir"]
    d = Path(info["log_dir"])
    assert (d / "process.log").is_file()
    assert (d / "events.jsonl").is_file()
    assert (d / "run.json").is_file()
    body = (d / "process.log").read_text(encoding="utf-8")
    assert "hello internal" in body
    assert "OUT|" in body or "ERR|" in body
    summary = json.loads((d / "run.json").read_text(encoding="utf-8"))
    assert summary["status"] == "ok"
    assert summary["agent"] == "claude"


def test_claude_verbose_flag(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GOAP_AGENT_CLI_VERBOSE", "1")
    d = ClaudeCliDriver()
    cmd = d.build_cmd("hi", options=CliRunOptions(verbose=True))
    assert "--verbose" in cmd
    cmd_off = d.build_cmd("hi", options=CliRunOptions(verbose=False))
    assert "--verbose" not in cmd_off


@pytest.mark.asyncio
async def test_run_cli_agent_writes_log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_BINARY", "claude")
    monkeypatch.setenv("GOAP_AGENT_LOG", "1")
    monkeypatch.setenv("GOAP_AGENT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("GOAP_AGENT_CLI_VERBOSE", "0")

    class FakeProc:
        returncode = 0
        pid = 4242
        stdout = None
        stderr = None

        async def communicate(self):
            return (
                b'{"result":"ok-from-cli","total_cost_usd":0.02,"session_id":"s1"}',
                b"internal: starting\ninternal: done\n",
            )

        def kill(self):
            pass

        async def wait(self):
            return 0

    with patch("core_graph.goap_agent.cli.runner.which_binary", return_value="/usr/bin/claude"):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.return_value = FakeProc()
            result = await run_cli_agent(
                "hello",
                agent="claude",
                options=CliRunOptions(log_enabled=True, verbose=False),
            )
    assert result.status == "ok"
    assert result.log
    assert result.log.get("process_log")
    pl = Path(result.log["process_log"])
    assert pl.is_file()
    text = pl.read_text(encoding="utf-8")
    assert "spawning subprocess" in text or "spawn" in text.lower()
    assert "ok-from-cli" in text or "result_text_preview" in text
    # stderr internal lines mirrored
    assert "internal" in text
    assert (Path(result.log["log_dir"]) / "stdout.txt").is_file()
    assert (Path(result.log["log_dir"]) / "stderr.txt").is_file()
    assert (Path(result.log["log_dir"]) / "run.json").is_file()


@pytest.mark.asyncio
async def test_run_cli_agent_streaming_pipes(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_BINARY", "claude")
    monkeypatch.setenv("GOAP_AGENT_LOG_DIR", str(tmp_path))

    class FakeStream:
        def __init__(self, data: bytes):
            self._lines = data.splitlines(keepends=True)
            self._i = 0

        async def readline(self):
            if self._i >= len(self._lines):
                return b""
            line = self._lines[self._i]
            self._i += 1
            return line

    class StreamProc:
        returncode = 0
        pid = 99

        def __init__(self):
            self.stdout = FakeStream(b'{"result":"streamed"}\n')
            self.stderr = FakeStream(b"dbg: step1\ndbg: step2\n")

        async def wait(self):
            return 0

        def kill(self):
            pass

    with patch("core_graph.goap_agent.cli.runner.which_binary", return_value="/usr/bin/claude"):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.return_value = StreamProc()
            result = await run_cli_agent(
                "stream me",
                agent="claude",
                options=CliRunOptions(log_enabled=True, verbose=False),
            )
    assert result.status == "ok"
    assert result.text == "streamed"
    body = Path(result.log["process_log"]).read_text(encoding="utf-8")
    assert "dbg: step1" in body
    assert "dbg: step2" in body
