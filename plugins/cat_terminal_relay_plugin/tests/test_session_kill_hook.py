"""Unit tests for SessionRegistry kill hooks."""

import pytest
from plugins.cat_terminal_relay_plugin.services.session_registry import SessionRegistry


@pytest.mark.asyncio
async def test_session_kill_hook_success():
    reg = SessionRegistry()
    killed_sessions = []

    def hook(session_id: str):
        killed_sessions.append(session_id)

    reg.add_kill_hook(hook)

    s = reg.create("alice", "ide1")
    session_id = s.session_id

    # Kill session and verify hook called
    result = await reg.kill(session_id)
    assert result is True
    assert killed_sessions == [session_id]


@pytest.mark.asyncio
async def test_session_kill_hook_unknown_id():
    reg = SessionRegistry()
    killed_sessions = []

    def hook(session_id: str):
        killed_sessions.append(session_id)

    reg.add_kill_hook(hook)

    # Kill non-existent session
    result = await reg.kill("unknown_session_id")
    assert result is False
    assert len(killed_sessions) == 0


@pytest.mark.asyncio
async def test_session_kill_hook_exception_ignored():
    reg = SessionRegistry()
    called = []

    def bad_hook(session_id: str):
        raise ValueError("Intentional error")

    def good_hook(session_id: str):
        called.append(session_id)

    reg.add_kill_hook(bad_hook)
    reg.add_kill_hook(good_hook)

    s = reg.create("alice", "ide1")
    session_id = s.session_id

    # The exception from bad_hook should be caught and not raise
    result = await reg.kill(session_id)
    assert result is True
    # The subsequent hooks should still be run
    assert called == [session_id]
