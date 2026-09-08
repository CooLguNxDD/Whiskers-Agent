import json
import pytest

from plugins.cat_terminal_relay_plugin.services.ide_registry import IdeRegistry


class _FakeWS:
    """Fake WebSocket captures sent text frames."""
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self._fail = fail

    async def send_text(self, text: str) -> None:
        if self._fail:
            raise RuntimeError("dead socket")
        self.sent.append(text)


def test_subscribe_unsubscribe():
    reg = IdeRegistry()
    ws = _FakeWS()
    
    assert len(reg._subscribers) == 0
    reg.subscribe(ws, "alice")
    assert reg._subscribers.get(ws) == "alice"
    
    reg.unsubscribe(ws)
    assert len(reg._subscribers) == 0
    
    # unsubscribe on non-existent is a no-op
    reg.unsubscribe(ws)


@pytest.mark.asyncio
async def test_register_broadcasts_to_same_subject():
    reg = IdeRegistry()
    ws_alice = _FakeWS()
    ws_bob = _FakeWS()

    reg.subscribe(ws_alice, "alice")
    reg.subscribe(ws_bob, "bob")

    # When an IDE registers for alice, only ws_alice should get the update
    ws_ide = _FakeWS()
    await reg.register("ide-alice", ws_ide, "alice")

    assert len(ws_alice.sent) == 1
    data_alice = json.loads(ws_alice.sent[0])
    assert data_alice["status"] == "ok"
    assert len(data_alice["hosts"]) == 1
    assert data_alice["hosts"][0]["ide_id"] == "ide-alice"

    # ws_bob shouldn't get the alice update
    assert len(ws_bob.sent) == 0


@pytest.mark.asyncio
async def test_unregister_broadcasts_to_same_subject():
    reg = IdeRegistry()
    ws_alice = _FakeWS()
    ws_bob = _FakeWS()

    ws_ide = _FakeWS()
    await reg.register("ide-alice", ws_ide, "alice")

    reg.subscribe(ws_alice, "alice")
    reg.subscribe(ws_bob, "bob")

    # Now unregister
    await reg.unregister("ide-alice")

    assert len(ws_alice.sent) == 1
    data_alice = json.loads(ws_alice.sent[0])
    assert data_alice["status"] == "ok"
    assert len(data_alice["hosts"]) == 0

    assert len(ws_bob.sent) == 0


@pytest.mark.asyncio
async def test_unregister_unknown_does_not_broadcast():
    reg = IdeRegistry()
    ws_alice = _FakeWS()
    reg.subscribe(ws_alice, "alice")

    # unregistering unknown host should NOT trigger broadcast
    await reg.unregister("unknown-ide")
    assert len(ws_alice.sent) == 0


@pytest.mark.asyncio
async def test_broadcast_dead_socket_cleanup():
    reg = IdeRegistry()
    ws_dead = _FakeWS(fail=True)
    ws_alive = _FakeWS()

    reg.subscribe(ws_dead, "alice")
    reg.subscribe(ws_alive, "alice")

    ws_ide = _FakeWS()
    await reg.register("ide-alice", ws_ide, "alice")

    # Since ws_dead failed, it should be removed from subscribers, but ws_alive should still succeed
    assert ws_dead not in reg._subscribers
    assert ws_alive in reg._subscribers
    assert len(ws_alive.sent) == 1
