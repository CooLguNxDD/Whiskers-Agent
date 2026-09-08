"""Unit tests for the terminal relay command guard."""

import pytest

from plugins.cat_terminal_relay_plugin import command_guard


@pytest.fixture
def allow(monkeypatch):
    """Populate the allowlist for the duration of a test."""
    monkeypatch.setattr(
        command_guard, "ALLOWED_BINARIES",
        frozenset({"git", "ls", "cat", "echo", "cd", "python"}),
    )


def test_empty_allowlist_denies_everything(monkeypatch):
    monkeypatch.setattr(command_guard, "ALLOWED_BINARIES", frozenset())
    v = command_guard.guard_line("ls -la")
    assert v["allowed"] is False
    assert v["reason"] == "binary_not_allowed"
    assert v["binary"] == "ls"


def test_allowlisted_binary_passes(allow):
    v = command_guard.guard_line("ls -la /tmp")
    assert v["allowed"] is True
    assert v["binary"] == "ls"
    assert v["reason"] is None


def test_cd_is_allowed_when_listed(allow):
    # cd is allowlisted for the relay because the PTY is stateful.
    assert command_guard.guard_line("cd src")["allowed"] is True


def test_non_allowlisted_binary_denied(allow):
    v = command_guard.guard_line("curl http://evil")
    assert v["allowed"] is False
    assert v["binary"] == "curl"


@pytest.mark.parametrize("line", [
    "ls; rm file",            # chain operator
    "echo a && echo b",       # &&
    "cat a | grep b",         # pipe
    "echo `whoami`",          # backtick substitution
    "cat $(id)",              # $() substitution
    "cat ../../etc/passwd",   # path traversal
    "echo x > /etc/hosts",    # redirect to absolute path
    "rm -rf /",               # rm -rf
    "rm  -fr build",          # rm -fr variant
])
def test_blocked_patterns(allow, line):
    v = command_guard.guard_line(line)
    assert v["allowed"] is False
    assert v["reason"].startswith("blocked_pattern:")


def test_parse_error_unbalanced_quote(allow):
    v = command_guard.guard_line('echo "unterminated')
    assert v["allowed"] is False
    assert v["reason"].startswith("parse_error:")


def test_blank_line_allowed_no_binary(allow):
    v = command_guard.guard_line("   ")
    assert v["allowed"] is True
    assert v["binary"] is None


def test_none_line_denied(allow):
    v = command_guard.guard_line(None)
    assert v["allowed"] is False
    assert v["reason"] == "empty_line"


def test_newline_blocked_in_guard_line(allow):
    v = command_guard.guard_line("ls\nrm -rf /")
    assert v["allowed"] is False
    assert v["reason"] == "blocked_pattern:newline"

