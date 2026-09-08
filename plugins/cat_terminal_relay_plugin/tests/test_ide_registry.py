"""Unit tests for IdeRegistry presence, session tracking, and control push."""

import json

from plugins.cat_terminal_relay_plugin.services.ide_registry import IdeRegistry


class _FakeWS:
    """Minimal stand-in capturing send_text payloads."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self._fail = fail

    async def send_text(self, text: str) -> None:
        if self._fail:
            raise RuntimeError("boom")
        self.sent.append(text)


async def test_register_and_list_filters_by_subject():
    reg = IdeRegistry()
    await reg.register("ide-a", _FakeWS(), "alice")
    await reg.register("ide-b", _FakeWS(), "bob")
    assert reg.is_online("ide-a")
    ids = {h["ide_id"] for h in reg.list_online(subject="alice")}
    assert ids == {"ide-a"}


async def test_unregister_removes_host():
    reg = IdeRegistry()
    await reg.register("ide-a", _FakeWS(), "alice")
    await reg.unregister("ide-a")
    assert not reg.is_online("ide-a")
    assert reg.list_online() == []


async def test_track_and_untrack_session():
    reg = IdeRegistry()
    await reg.register("ide-a", _FakeWS(), "alice")
    reg.track_session("ide-a", "sess-1")
    assert reg.get("ide-a")["session_ids"] == {"sess-1"}
    reg.untrack_session("ide-a", "sess-1")
    assert reg.get("ide-a")["session_ids"] == set()


async def test_send_control_serializes_to_host():
    reg = IdeRegistry()
    ws = _FakeWS()
    await reg.register("ide-a", ws, "alice")
    ok = await reg.send_control("ide-a", {"type": "open", "session_id": "s1"})
    assert ok is True
    assert json.loads(ws.sent[0]) == {"type": "open", "session_id": "s1"}


async def test_send_control_offline_returns_false():
    reg = IdeRegistry()
    assert await reg.send_control("nope", {"type": "kill", "session_id": "s1"}) is False


async def test_send_control_swallows_send_failure():
    reg = IdeRegistry()
    await reg.register("ide-a", _FakeWS(fail=True), "alice")
    assert await reg.send_control("ide-a", {"type": "open", "session_id": "s1"}) is False
