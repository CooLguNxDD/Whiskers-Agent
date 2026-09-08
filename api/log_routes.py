"""




Server log streaming routes.


Each SSE frame carries:
  { "t": "HH:MM:SS", "tag": "SYS", "cls": "sys", "msg": "...", "level": "INFO" }

Tag/cls mapping (derived from Python log level + message prefix):
  DEBUG   → DBG  / dbg
  INFO    → SYS  / sys  (unless prefixed REQ/OK → tagged accordingly)
  WARNING → WARN / warn
  ERROR   → ERR  / err
  CRITICAL→ ERR  / err

Endpoints
---------
GET    /api/logs/session_gated/recent   — Return the last N log entries as a JSON snapshot (no streaming).
GET    /api/logs/session_gated/stream   — Open an SSE stream of live whiskers logger output.
"""

import asyncio
import itertools
import json
import logging
import time
from collections import deque
from typing import Deque

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from core.context import mcp

logger = logging.getLogger("whiskers")

# ---------------------------------------------------------------------------
# In-process log capture
# ---------------------------------------------------------------------------

_MAX_QUEUE_SIZE = 200
_MAX_HISTORY = 100

# Ring buffer of the last N entries (for /recent snapshot)
_history: Deque[dict] = deque(maxlen=_MAX_HISTORY)
_log_seq = itertools.count(1)

# Active SSE subscriber queues
_subscribers: list[asyncio.Queue] = []

# Event loop the SSE queues belong to (set on first use from within the loop)
_main_loop: asyncio.AbstractEventLoop | None = None

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _level_to_tag(levelno: int, msg: str) -> tuple[str, str]:
    """Derive (tag, css-class) from Python log level + message heuristics."""
    msg_upper = msg.upper()

    if levelno >= logging.ERROR:
        return "ERR", "err"
    if levelno >= logging.WARNING:
        return "WARN", "warn"
    if levelno <= logging.DEBUG:
        return "DBG", "dbg"

    # INFO — peek at message for protocol-level hints
    if msg_upper.startswith(("POST ", "GET ", "PUT ", "DELETE ", "PATCH ")):
        return "REQ", "req"
    if msg_upper.startswith(("200 ", "201 ", "204 ", "OK ", "202 ")):
        return " OK", "ok"
    return "SYS", "sys"


def _fanout(entry: dict) -> None:
    """Push a log entry onto every subscriber queue. Must run on the event loop thread."""
    dead: list[asyncio.Queue] = []
    for q in list(_subscribers):
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            # Drop oldest item to make room
            try:
                q.get_nowait()
                q.put_nowait(entry)
            except Exception:
                logger.debug("log_routes.py: swallowed exception", exc_info=True)
        except Exception as exc:
            logger.warning("_fanout: subscriber queue put failed, marking dead: %s", exc)
            dead.append(q)

    for q in dead:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


class _QueueHandler(logging.Handler):
    """Logging handler that fans log records out to all SSE subscriber queues."""

    def emit(self, record: logging.LogRecord) -> None:
        """Format and enqueue a log record, fanning out to active SSE queues."""
        try:
            msg = self.format(record)
            now = time.localtime()
            ts = f"{now.tm_hour:02d}:{now.tm_min:02d}:{now.tm_sec:02d}"
            tag, cls = _level_to_tag(record.levelno, msg)

            entry = {
                "id": next(_log_seq),
                "t": ts,
                "tag": tag,
                "cls": cls,
                "msg": msg,
                "level": record.levelname,
            }
            _history.append(entry)

            global _main_loop
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop is not None:
                _main_loop = running_loop
                _fanout(entry)
            elif _main_loop is not None and _main_loop.is_running():
                _main_loop.call_soon_threadsafe(_fanout, entry)
            # else: no loop available yet — entry stays in _history only

        except Exception:  # noqa: BLE001
            # Never let the log handler crash the server
            logger.debug("log_routes: SSE fanout handler swallowed exception", exc_info=True)


# Attach the handler once at import time (idempotent — guard by name)
_root_logger = logging.getLogger("whiskers")
_legacy_logger = logging.getLogger("whiskers_agent")
_handler_name = "_whiskers_agent_sse_handler"
_handler = _QueueHandler()
_handler._whiskers_tag = _handler_name  # type: ignore[attr-defined]
_handler.setFormatter(logging.Formatter("%(name)s — %(message)s"))
_handler.setLevel(logging.DEBUG)

if not any(getattr(h, "_whiskers_tag", None) == _handler_name for h in _root_logger.handlers):
    _root_logger.addHandler(_handler)
if not any(getattr(h, "_whiskers_tag", None) == _handler_name for h in _legacy_logger.handlers):
    _legacy_logger.addHandler(_handler)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@http_route_registry.route(
    route="logs",
    endpoint="stream",
    methods=["GET"],
    name="api_logs_stream",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.log",
)
async def log_stream(request: Request) -> Response:
    """Open an SSE stream of live whiskers logger output.

    The client must be authenticated (session cookie checked by
    SessionGateMiddleware which wraps all /api/* routes).
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
    _subscribers.append(q)

    async def event_generator():
        """Generate log events to stream to the SSE client."""
        # Flush the most recent history entries first so the terminal isn't blank
        for entry in list(_history):
            yield f"data: {json.dumps(entry)}\n\n"

        try:
            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(entry)}\n\n"
                except asyncio.TimeoutError:
                    # Send a heartbeat comment to keep the connection alive
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            try:
                _subscribers.remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@http_route_registry.route(
    route="logs",
    endpoint="recent",
    methods=["GET"],
    name="api_logs_recent",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.log",
)
async def log_recent(request: Request) -> Response:
    """Return the last N log entries as a JSON snapshot (no streaming)."""
    limit_raw = request.query_params.get("limit", "50")
    try:
        limit = max(1, min(int(limit_raw), _MAX_HISTORY))
    except ValueError:
        limit = 50

    entries = list(_history)[-limit:]
    return JSONResponse({"entries": entries, "total": len(_history)})
