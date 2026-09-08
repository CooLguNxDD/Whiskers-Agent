"""
Integration test for the Cat Terminal Relay byte pump + handshake auth (Phase 2).

Runs a real uvicorn server (in the test's own event loop, so the relay's session
state stays single-loop, exactly as in production where the session is created by
an HTTP handler on the same loop) exposing the three WebSocket legs: the browser
console, the extension CONTROL leg, and the per-session extension DATA leg. A mock
"extension" connects the control leg, receives an ``open`` control frame, dials a
data leg, and we assert bytes round-trip console ↔ relay ↔ data leg — including the
multi-session case (two sessions over one control leg) and handshake rejection.

Uses the CAT_TERMINAL_DEV_AUTH seam so no RS256 keypair infra is required.
"""

import asyncio
import contextlib
import json
import socket

import pytest

websockets = pytest.importorskip("websockets")
import uvicorn  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.routing import WebSocketRoute  # noqa: E402

from plugins.cat_terminal_relay_plugin.routes.console_ws import (  # noqa: E402
    PATH as CONSOLE_PATH,
    console_ws,
)
from plugins.cat_terminal_relay_plugin.routes.extension_ws import (  # noqa: E402
    PATH as EXTENSION_PATH,
    extension_ws,
)
from plugins.cat_terminal_relay_plugin.routes.data_ws import (  # noqa: E402
    PATH as DATA_PATH,
    data_ws,
)
from plugins.cat_terminal_relay_plugin.services import (  # noqa: E402
    ide_registry,
    session_registry,
)
from plugins.cat_terminal_relay_plugin.services.session_ops import (  # noqa: E402
    open_relay_session,
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.asynccontextmanager
async def _running_server():
    app = Starlette(routes=[
        WebSocketRoute(CONSOLE_PATH, console_ws),
        WebSocketRoute(DATA_PATH, data_ws),
        WebSocketRoute(EXTENSION_PATH, extension_ws),
    ])
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started, "uvicorn did not start"
        yield port
    finally:
        server.should_exit = True
        await task


async def _wait_online(ide_id: str, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if ide_registry.is_online(ide_id):
            return True
        await asyncio.sleep(0.01)
    return False


async def _open_and_attach(base, ide_id, control, subject="alice"):
    """Open a session via session_ops, read the control frame, dial the data leg."""
    result = await open_relay_session(subject, ide_id)
    assert result["status"] == "ok", result
    session = result["session"]
    frame = json.loads(await asyncio.wait_for(control.recv(), timeout=2))
    assert frame == {"type": "open", "session_id": session.session_id, "workdir": None}
    data_url = (
        f"{base}/api/terminal/none/host/{ide_id}/session/{session.session_id}"
        f"?token=dev:{subject}:terminal:host"
    )
    data = await websockets.connect(data_url)
    return session, data


async def test_byte_pump_round_trip(monkeypatch):
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")
    async with _running_server() as port:
        base = f"ws://127.0.0.1:{port}"
        ctrl_url = f"{base}/api/terminal/none/host/ide-1?token=dev:alice:terminal:host"

        async with websockets.connect(ctrl_url) as control:
            assert await _wait_online("ide-1")
            session, data = await _open_and_attach(base, "ide-1", control)
            try:
                con_url = f"{base}/api/terminal/none/ws/{session.session_id}?token=dev:alice:terminal:use"
                async with websockets.connect(con_url) as con:
                    await con.send("hello-pty")
                    assert await asyncio.wait_for(data.recv(), timeout=2) == "hello-pty"
                    await data.send("pty-says-hi")
                    assert await asyncio.wait_for(con.recv(), timeout=2) == "pty-says-hi"
            finally:
                await data.close()
        session_registry.remove(session.session_id)


async def test_console_reconnect_preserves_session(monkeypatch):
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")
    async with _running_server() as port:
        base = f"ws://127.0.0.1:{port}"
        ctrl_url = f"{base}/api/terminal/none/host/ide-reconnect?token=dev:alice:terminal:host"

        async with websockets.connect(ctrl_url) as control:
            assert await _wait_online("ide-reconnect")
            session, data = await _open_and_attach(base, "ide-reconnect", control)
            try:
                con_url = f"{base}/api/terminal/none/ws/{session.session_id}?token=dev:alice:terminal:use"
                async with websockets.connect(con_url) as con1:
                    await con1.send("hello-pty-1")
                    assert await asyncio.wait_for(data.recv(), timeout=2) == "hello-pty-1"
                
                # con1 is now closed. Check that session is still registry and alive.
                assert session_registry.get(session.session_id) is not None
                
                # Reconnect
                async with websockets.connect(con_url) as con2:
                    await data.send("pty-says-hi-again")
                    assert await asyncio.wait_for(con2.recv(), timeout=2) == "pty-says-hi-again"
            finally:
                await data.close()
        session_registry.remove(session.session_id)


async def test_multi_session_independent_pumps(monkeypatch):
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")
    async with _running_server() as port:
        base = f"ws://127.0.0.1:{port}"
        ctrl_url = f"{base}/api/terminal/none/host/ide-m?token=dev:bob:terminal:host"

        async with websockets.connect(ctrl_url) as control:
            assert await _wait_online("ide-m")
            s1, d1 = await _open_and_attach(base, "ide-m", control, subject="bob")
            s2, d2 = await _open_and_attach(base, "ide-m", control, subject="bob")
            try:
                c1 = await websockets.connect(
                    f"{base}/api/terminal/none/ws/{s1.session_id}?token=dev:bob:terminal:use")
                c2 = await websockets.connect(
                    f"{base}/api/terminal/none/ws/{s2.session_id}?token=dev:bob:terminal:use")
                try:
                    # Each console reaches only its own data leg.
                    await c1.send("one")
                    await c2.send("two")
                    assert await asyncio.wait_for(d1.recv(), timeout=2) == "one"
                    assert await asyncio.wait_for(d2.recv(), timeout=2) == "two"
                finally:
                    await c1.close()
                    await c2.close()
            finally:
                await d1.close()
                await d2.close()
        session_registry.remove(s1.session_id)
        session_registry.remove(s2.session_id)


async def test_console_rejects_subject_mismatch(monkeypatch):
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")
    async with _running_server() as port:
        base = f"ws://127.0.0.1:{port}"
        async with websockets.connect(f"{base}/api/terminal/none/host/ide-2?token=dev:alice:terminal:host") as control:
            assert await _wait_online("ide-2")
            session, data = await _open_and_attach(base, "ide-2", control)
            try:
                bad = f"{base}/api/terminal/none/ws/{session.session_id}?token=dev:mallory:terminal:use"
                with pytest.raises(Exception):
                    async with websockets.connect(bad):
                        pass
            finally:
                await data.close()
        session_registry.remove(session.session_id)


async def test_data_leg_accepts_write_scope_via_implication(monkeypatch):
    """terminal:use maps to core:terminal:write, which implies core:terminal:read
    (== terminal:host) via grammar.expand_implied — same as every other core
    domain's write-implies-read rule. A write-scoped token must connect on the
    data leg, not be rejected (previously rejected: the handshake's inline
    scope check didn't apply grammar implication — see test_terminal_api_key_auth.py's
    equivalent write-implies-read regression test)."""
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")
    async with _running_server() as port:
        base = f"ws://127.0.0.1:{port}"
        async with websockets.connect(f"{base}/api/terminal/none/host/ide-3?token=dev:alice:terminal:host") as control:
            assert await _wait_online("ide-3")
            result = await open_relay_session("alice", "ide-3")
            session = result["session"]
            await asyncio.wait_for(control.recv(), timeout=2)  # drain open frame
            ok = (f"{base}/api/terminal/none/host/ide-3/session/{session.session_id}"
                  f"?token=dev:alice:terminal:use")
            async with websockets.connect(ok):
                pass
        session_registry.remove(session.session_id)


async def test_data_leg_rejects_unrelated_scope(monkeypatch):
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")
    async with _running_server() as port:
        base = f"ws://127.0.0.1:{port}"
        async with websockets.connect(f"{base}/api/terminal/none/host/ide-3b?token=dev:alice:terminal:host") as control:
            assert await _wait_online("ide-3b")
            result = await open_relay_session("alice", "ide-3b")
            session = result["session"]
            await asyncio.wait_for(control.recv(), timeout=2)  # drain open frame
            # An unrelated scope (no terminal grant at all) still gets 4401.
            bad = (f"{base}/api/terminal/none/host/ide-3b/session/{session.session_id}"
                   f"?token=dev:alice:core:graph:read")
            with pytest.raises(Exception):
                async with websockets.connect(bad):
                    pass
        session_registry.remove(session.session_id)


async def test_handshake_rejects_missing_scope(monkeypatch):
    monkeypatch.setenv("CAT_TERMINAL_DEV_AUTH", "1")
    async with _running_server() as port:
        base = f"ws://127.0.0.1:{port}"
        # terminal:host token on the console leg (which requires terminal:use) → 4401.
        bad = f"{base}/api/terminal/none/ws/whatever?token=dev:alice:terminal:host"
        with pytest.raises(Exception):
            async with websockets.connect(bad):
                pass
