"""Unit tests for CLI subprocess privilege drop (whiskers-claude)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="CLI user drop requires POSIX / Linux Docker")

from core_graph.goap_agent.cli.base import CliRunOptions
from core_graph.goap_agent.cli.claude import ClaudeCliDriver, _should_skip_permissions
from core_graph.goap_agent.cli.user_drop import (
    DEFAULT_CLI_USER,
    CliRunUser,
    apply_user_env,
    configured_cli_user_name,
    ensure_readable_by_user,
    make_preexec_fn,
    resolve_cli_run_user,
)


def test_default_user_when_root(monkeypatch):
    monkeypatch.delenv("CLI_AGENT_USER", raising=False)
    monkeypatch.delenv("GOAP_AGENT_CLI_USER", raising=False)
    monkeypatch.setenv("CLI_AGENT_DROP_PRIVS", "1")
    with patch("os.geteuid", return_value=0):
        assert configured_cli_user_name() == DEFAULT_CLI_USER


def test_drop_disabled(monkeypatch):
    monkeypatch.delenv("CLI_AGENT_USER", raising=False)
    monkeypatch.setenv("CLI_AGENT_DROP_PRIVS", "0")
    with patch("os.geteuid", return_value=0):
        assert configured_cli_user_name() is None


def test_explicit_empty_disables(monkeypatch):
    monkeypatch.setenv("CLI_AGENT_USER", "")
    assert configured_cli_user_name() is None


def test_resolve_missing_user(monkeypatch):
    monkeypatch.setenv("CLI_AGENT_USER", "definitely-not-a-real-user-xyz")
    assert resolve_cli_run_user() is None


def test_apply_user_env_sets_home():
    u = CliRunUser(name="whiskers-claude", uid=999, gid=999, home="/home/whiskers-claude")
    env = apply_user_env({"PATH": "/usr/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t"}, u)
    assert env["HOME"] == "/home/whiskers-claude"
    assert env["USER"] == "whiskers-claude"
    assert env["CLAUDE_CONFIG_DIR"].endswith(".claude")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "t"


def test_skip_permissions_when_drop_user_nonroot(monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_SKIP_PERMISSIONS", "1")
    fake = CliRunUser(name="whiskers-claude", uid=10001, gid=10001, home="/home/whiskers-claude")
    with patch(
        "core_graph.goap_agent.cli.user_drop.resolve_cli_run_user",
        return_value=fake,
    ):
        # Parent may still be root — flag should still be on because child is non-root.
        with patch("os.geteuid", return_value=0):
            assert _should_skip_permissions(run_as_user="whiskers-claude") is True


def test_skip_permissions_off_for_root_child(monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_SKIP_PERMISSIONS", "1")
    fake = CliRunUser(name="root", uid=0, gid=0, home="/root")
    with patch(
        "core_graph.goap_agent.cli.user_drop.resolve_cli_run_user",
        return_value=fake,
    ):
        with patch("os.geteuid", return_value=0):
            assert _should_skip_permissions(run_as_user="root") is False


def test_claude_cmd_includes_skip_when_run_as_nonroot(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CLI_SKIP_PERMISSIONS", "1")
    fake = CliRunUser(name="whiskers-claude", uid=10001, gid=10001, home="/home/whiskers-claude")
    with patch(
        "core_graph.goap_agent.cli.user_drop.resolve_cli_run_user",
        return_value=fake,
    ):
        d = ClaudeCliDriver()
        cmd = d.build_cmd(
            "hi",
            options=CliRunOptions(run_as_user="whiskers-claude"),
        )
    assert "--dangerously-skip-permissions" in cmd


def test_make_preexec_fn_none_when_not_root():
    u = CliRunUser(name="whiskers-claude", uid=10001, gid=10001, home="/home/x")
    with patch("os.geteuid", return_value=1000):
        assert make_preexec_fn(u) is None


def test_make_preexec_fn_when_root():
    u = CliRunUser(name="whiskers-claude", uid=10001, gid=10001, home="/home/x")
    with patch("os.geteuid", return_value=0):
        fn = make_preexec_fn(u)
    assert callable(fn)


def test_ensure_readable_chowns_goap_agent_parent(tmp_path, monkeypatch):
    """Drop user must traverse mkdtemp-style goap_agent_* parents."""
    parent = tmp_path / "goap_agent_cli_testdir"
    parent.mkdir(mode=0o700)
    leaf = parent / "CLI_AGENT.md"
    leaf.write_text("hi\n", encoding="utf-8")
    os.chmod(leaf, 0o600)

    u = CliRunUser(name="whiskers-claude", uid=10001, gid=10001, home="/home/x")
    chowned: list[str] = []

    def _fake_chown(path, uid, gid):
        chowned.append(str(path))

    monkeypatch.setattr(os, "chown", _fake_chown)
    with patch("core_graph.goap_agent.cli.user_drop.is_root_uid", return_value=True):
        ensure_readable_by_user(leaf, u)

    assert any("goap_agent_cli_testdir" in p for p in chowned)
    assert any(p.endswith("CLI_AGENT.md") for p in chowned)
    # Parent must be traversable after fix.
    assert (parent.stat().st_mode & 0o111) != 0
