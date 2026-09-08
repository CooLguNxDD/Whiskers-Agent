"""
Shared relay-session open/kill logic.

Both the MCP tools (Bearer-JWT callers) and the cookie-authed REST control plane
funnel through here so the ide-online check, subject binding, session creation,
and control-channel push stay in one place. Subject is always supplied by the
caller after it has authenticated; these helpers never authenticate.
"""

import logging

from utils.error_response import tool_error
from . import ide_registry, session_registry
from .relay_session import RelaySession

logger = logging.getLogger("whiskers.plugins")


async def open_relay_session(
    subject: str, ide_id: str, workdir: str | None = None
) -> dict:
    """Create a session on an online host and push an ``open`` control frame.

    Returns ``{"status":"ok","session":RelaySession}`` or a structured error dict.
    """
    if not ide_registry.is_online(ide_id):
        return tool_error("ide_offline", f"IDE host '{ide_id}' is not online.")
    host = ide_registry.get(ide_id)
    if host is not None and host.get("subject") != subject:
        return tool_error("forbidden", "IDE host belongs to a different operator.")

    session: RelaySession = session_registry.create(subject, ide_id, workdir)
    ide_registry.track_session(ide_id, session.session_id)
    pushed = await ide_registry.send_control(
        ide_id,
        {"type": "open", "session_id": session.session_id, "workdir": workdir},
    )
    if not pushed:
        await session_registry.kill(session.session_id)
        ide_registry.untrack_session(ide_id, session.session_id)
        return tool_error("ide_offline", f"IDE host '{ide_id}' is not reachable.")
    logger.info("AUDIT terminal_session_opened subject=%s ide=%s session=%s workdir=%s",
                subject, ide_id, session.session_id, workdir)
    return {"status": "ok", "session": session}


async def kill_relay_session(subject: str, session_id: str) -> dict:
    """Subject-checked kill: tear down legs and push a ``kill`` control frame."""
    session = session_registry.get(session_id)
    if session is None:
        return tool_error("not_found", f"No such session: {session_id}.")
    if session.subject != subject:
        return tool_error("forbidden", "Session belongs to a different operator.")
    ide_id = session.ide_id
    await session_registry.kill(session_id)
    await ide_registry.send_control(ide_id, {"type": "kill", "session_id": session_id})
    logger.info("AUDIT terminal_session_killed subject=%s session=%s", subject, session_id)
    return {"status": "ok", "session_id": session_id, "killed": True}
