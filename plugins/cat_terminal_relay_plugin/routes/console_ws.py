"""
Console (browser) WebSocket leg — /terminal/ws/{session_id}.

The browser dials in with a terminal:use JWT. The leg is rejected unless the
token validates, carries terminal:use, the session exists, and the session's
bound subject matches the token subject (anti-hijack).
"""

import logging

from starlette.websockets import WebSocket

from ..services import session_registry
from .auth import authenticate_handshake

logger = logging.getLogger("whiskers.plugins")

PATH = "/api/terminal/none/ws/{session_id}"
REQUIRED_SCOPE = "core:terminal:write"


async def console_ws(websocket: WebSocket) -> None:
    """
    WebSocket route for terminal console.
    """
    session_id = websocket.path_params["session_id"]
    subject = await authenticate_handshake(websocket, REQUIRED_SCOPE)
    if subject is None:
        await websocket.close(code=4401)  # unauthorized
        return

    session = session_registry.get(session_id)
    if session is None or session.subject != subject:
        # No such session, or subject bound to a different operator → hijack guard.
        await websocket.close(code=4403)  # forbidden
        return

    await websocket.accept()
    logger.info("console leg attached: session=%s subject=%s", session_id, subject)
    await session.attach_console(websocket)
