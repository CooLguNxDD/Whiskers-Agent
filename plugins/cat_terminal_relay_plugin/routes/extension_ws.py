"""
Extension CONTROL WebSocket leg — /terminal/host/{ide_id}.

The VS Code extension dials OUTBOUND with a terminal:host JWT and holds this
control connection (Docker never reaches the host). The relay pushes JSON control
frames over it — ``{"type":"open", session_id, workdir}`` / ``{"type":"kill",
session_id}`` — and the extension reacts by spawning/killing a PTY and dialling a
separate per-session DATA leg. This leg carries no PTY bytes; it is a presence +
signalling channel. On disconnect every session bound to the host is killed.
"""

import logging

from starlette.websockets import WebSocket, WebSocketDisconnect

from ..services import ide_registry, session_registry
from .auth import authenticate_handshake

logger = logging.getLogger("whiskers.plugins")

PATH = "/api/terminal/none/host/{ide_id}"
REQUIRED_SCOPE = "core:terminal:read"


async def extension_ws(websocket: WebSocket) -> None:
    """
    WebSocket route for extension communication.
    """
    ide_id = websocket.path_params["ide_id"]
    subject = await authenticate_handshake(websocket, REQUIRED_SCOPE)
    if subject is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    await ide_registry.register(ide_id, websocket, subject)
    logger.info("extension control leg online: ide=%s subject=%s", ide_id, subject)
    try:
        # Hold the control leg open; drain inbound frames (acks/audit) until close.
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("extension control leg error (ide=%s): %s", ide_id, exc)
    finally:
        entry = ide_registry.get(ide_id)
        session_ids = list(entry["session_ids"]) if entry else []
        await ide_registry.unregister(ide_id)
        for sid in session_ids:
            await session_registry.kill(sid)
