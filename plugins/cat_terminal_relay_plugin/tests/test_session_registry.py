"""Unit tests for SessionRegistry binding + cap + reaper."""

from plugins.cat_terminal_relay_plugin.services.session_registry import SessionRegistry


def test_create_binds_subject_and_ide():
    reg = SessionRegistry()
    s = reg.create("alice", "ide-1", workdir="/repo")
    assert reg.get(s.session_id) is s
    assert s.subject == "alice"
    assert s.ide_id == "ide-1"
    assert s.workdir == "/repo"


def test_per_subject_cap_evicts_oldest():
    reg = SessionRegistry(max_per_subject=1)
    first = reg.create("bob", "ide-1")
    second = reg.create("bob", "ide-2")
    # Cap=1 → the first session is evicted on the second create.
    assert reg.get(first.session_id) is None
    assert reg.get(second.session_id) is second
    assert len(reg.list_for_subject("bob")) == 1


def test_different_subjects_coexist():
    reg = SessionRegistry(max_per_subject=1)
    a = reg.create("alice", "ide-1")
    b = reg.create("bob", "ide-2")
    assert reg.get(a.session_id) is a
    assert reg.get(b.session_id) is b


async def test_kill_removes_session():
    reg = SessionRegistry()
    s = reg.create("carol", "ide-1")
    assert await reg.kill(s.session_id) is True
    assert reg.get(s.session_id) is None
    # Killing again is a no-op (False).
    assert await reg.kill(s.session_id) is False


async def test_reaper_kills_idle_sessions():
    reg = SessionRegistry(idle_ttl=0, absolute_ttl=999999)
    s = reg.create("dave", "ide-1")
    await reg._reap_once()
    assert reg.get(s.session_id) is None
