import asyncio
import json
import logging
from starlette.websockets import WebSocket, WebSocketDisconnect

from ..services import ide_registry
from .auth import authenticate_handshake

logger = logging.getLogger("whiskers.plugins")

PATH = "/api/terminal/none/hosts/ws"
REQUIRED_SCOPE = "core:terminal:write"


async def hosts_ws(websocket: WebSocket) -> None:
    """
    WebSocket route for listening to online terminal hosts list updates.
    """
    subject = await authenticate_handshake(websocket, REQUIRED_SCOPE)
    if subject is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    ide_registry.subscribe(websocket, subject)
    try:
        # Send initial snapshot immediately
        initial_payload = json.dumps({"status": "ok", "hosts": ide_registry.list_online(subject=subject)})
        await websocket.send_text(initial_payload)

        # Heartbeat: timeout on receive + app-level ping probe to detect half-open/dead clients promptly.
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive(), timeout=60.0)
                if msg.get("type") == "websocket.disconnect":
                    break
            except asyncio.TimeoutError:
                # Probe: sending will fail fast if the peer is gone
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        pass
    except Exception as exc:
        logger.error("Hosts WS error: %s", exc)
    finally:
        ide_registry.unsubscribe(websocket)
