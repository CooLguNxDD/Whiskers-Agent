"""Unit tests for privileged command classifier in the terminal relay."""

import pytest
from plugins.cat_terminal_relay_plugin import command_guard


@pytest.mark.parametrize(
    "line,expected",
    [
        ("claude", True),
        ("claude -p 'hi'", True),
        ("codex", True),
        ("codex -p x", True),
        ("agy -p goals/x.md", True),
        ("ls", False),
        ("git status", False),
        ("echo claude", False),
        ("agy --version", False),
        ("", False),
        ("   ", False),
    ],
)
def test_classify_privileged(monkeypatch, line, expected):
    monkeypatch.setattr(command_guard, "PRIVILEGED_BINARIES", frozenset({"claude", "codex"}))
    assert command_guard.classify_privileged(line) is expected


def test_guard_line_privileged_behavior(monkeypatch):
    monkeypatch.setattr(
        command_guard,
        "ALLOWED_BINARIES",
        frozenset({"claude", "ls"}),
    )

    # Allowed and privileged
    v1 = command_guard.guard_line("claude -p x")
    assert v1["allowed"] is True
    assert v1["privileged"] is True
    assert v1["binary"] == "claude"
    assert v1["reason"] is None

    # Allowed but not privileged
    v2 = command_guard.guard_line("ls")
    assert v2["allowed"] is True
    assert v2["privileged"] is False
    assert v2["binary"] == "ls"
    assert v2["reason"] is None

    # Blocked (not allowed and not privileged)
    v3 = command_guard.guard_line("rm -rf /")
    assert v3["allowed"] is False
    assert v3["privileged"] is False
    assert v3["reason"].startswith("blocked_pattern:")
