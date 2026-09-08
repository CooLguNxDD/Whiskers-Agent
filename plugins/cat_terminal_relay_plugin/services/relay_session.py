"""
RelaySession — a single browser↔IDE terminal bridge.

Holds two WebSocket legs (console = browser, extension = host PTY) and pumps
frames between them. The relay is a DUMB PIPE: it never interprets the stream.
Command filtering happens at the extension (closest to execution). Each leg runs
its own receive loop; when either leg closes, the whole session tears down.
"""

import logging
import time

from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger("whiskers.plugins")


class RelaySession:
    """Two-legged byte relay bound to a subject and an IDE host."""

    def __init__(
        self,
        session_id: str,
        subject: str,
        ide_id: str,
        workdir: str | None = None,
    ) -> None:
        """Initialize the RelaySession with session, subject, and IDE identifier details."""
        self.session_id = session_id
        self.subject = subject
        self.ide_id = ide_id
        self.workdir = workdir
        self.created_at = time.monotonic()
        self.last_activity = self.created_at
        self.console: WebSocket | None = None
        self.extension: WebSocket | None = None
        self._closed = False
        self._on_close = None  # set by SessionRegistry to deregister
        self.bytes_total = 0

    # -- lifecycle -----------------------------------------------------------

    def touch(self) -> None:
        """Mark activity for idle-TTL accounting."""
        self.last_activity = time.monotonic()

    def idle_seconds(self) -> float:
        """Return elapsed seconds since the last registered activity."""
        return time.monotonic() - self.last_activity

    def age_seconds(self) -> float:
        """Return elapsed seconds since session creation."""
        return time.monotonic() - self.created_at

    async def attach_console(self, ws: WebSocket) -> None:
        """Attach the browser leg and pump until it closes (blocks)."""
        self.console = ws
        self.touch()
        await self._pump(ws, lambda: self.extension, leg="console")

    async def attach_extension(self, ws: WebSocket) -> None:
        """Attach the host PTY leg and pump until it closes (blocks)."""
        self.extension = ws
        self.touch()
        await self._pump(ws, lambda: self.console, leg="extension")

    async def _pump(self, src: WebSocket, get_dest, leg: str) -> None:
        """Forward every frame from ``src`` to the other leg until disconnect."""
        try:
            while not self._closed:
                msg = await src.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                self.touch()
                
                # Record relay traffic telemetry
                nbytes = 0
                if msg.get("text") is not None:
                    nbytes = len(msg["text"].encode("utf-8") if isinstance(msg["text"], str) else msg["text"])
                elif msg.get("bytes") is not None:
                    nbytes = len(msg["bytes"])
                self.bytes_total += nbytes
                try:
                    from core.telemetry import collector
                    collector.record_relay_traffic(self.session_id, leg, nbytes)
                except Exception:
                    logger.debug("relay_session.py: swallowed exception", exc_info=True)

                dest = get_dest()
                if dest is None:
                    # Other leg not connected yet — dumb pipe drops the frame.
                    continue
                try:
                    if msg.get("text") is not None:
                        await dest.send_text(msg["text"])
                    elif msg.get("bytes") is not None:
                        await dest.send_bytes(msg["bytes"])
                except Exception:
                    break
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning("relay pump error (session=%s leg=%s): %s",
                           self.session_id, leg, exc)
        finally:
            if leg == "console":
                self.console = None
                logger.info("console leg detached (session=%s)", self.session_id)
                if self.extension is None:
                    await self.close()
            else:
                await self.close()

    async def close(self) -> None:
        """Tear down both legs and deregister (idempotent)."""
        if self._closed:
            return
        self._closed = True
        for leg in (self.console, self.extension):
            if leg is not None:
                try:
                    await leg.close()
                except Exception as exc:
                    logger.debug("relay session %s: leg close failed: %s", self.session_id, exc)
        if self._on_close is not None:
            try:
                self._on_close(self.session_id)
            except Exception as exc:
                logger.warning("relay session %s: on_close callback failed: %s", self.session_id, exc)
        logger.info("relay session closed: %s (subject=%s ide=%s)",
                    self.session_id, self.subject, self.ide_id)
