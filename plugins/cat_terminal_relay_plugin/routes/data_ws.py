"""
Extension DATA WebSocket leg — /terminal/host/{ide_id}/session/{session_id}.

After the relay pushes an ``open`` control frame, the extension spawns a PTY and
dials this per-session leg with a terminal:host JWT. The handler validates the
bearer + scope + subject binding, then attaches as the session's extension leg
and pumps raw PTY bytes (dumb pipe) until close. One data leg per session.
"""

import logging

from starlette.websockets import WebSocket

from ..services import ide_registry, session_registry
from .auth import authenticate_handshake

logger = logging.getLogger("whiskers.plugins")

PATH = "/api/terminal/none/host/{ide_id}/session/{session_id}"
REQUIRED_SCOPE = "core:terminal:read"


async def data_ws(websocket: WebSocket) -> None:
    """
    WebSocket route for data stream.
    """
    ide_id = websocket.path_params["ide_id"]
    session_id = websocket.path_params["session_id"]
    subject = await authenticate_handshake(websocket, REQUIRED_SCOPE)
    if subject is None:
        await websocket.close(code=4401)
        return

    session = session_registry.get(session_id)
    if session is None or session.subject != subject or session.ide_id != ide_id:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    ide_registry.track_session(ide_id, session_id)
    logger.info("extension data leg attached: ide=%s session=%s subject=%s",
                ide_id, session_id, subject)
    try:
        await session.attach_extension(websocket)
    finally:
        ide_registry.untrack_session(ide_id, session_id)
