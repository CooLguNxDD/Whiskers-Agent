"""Fleet chat tools — MCP proxy over the Cat Fleet hub.

``agent_name`` is self-declared and stored verbatim. These tools do not stamp
a caller identity. Call ``fleet_get_messages`` at the start of a task and
again after you finish a step. Park on ``fleet_wait_for_mentions`` only when
you are idle and waiting to be mentioned; that tool is hidden from GOAP.
``fleet_wait_for_events`` is the broader park (mentions, task assignments,
channel lifecycle) and is hidden from GOAP for the same reason.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any

from pydantic import Field

from core.context import mcp
from utils.response_shape_hints import SHAPE_PARAM_DESCRIPTION

from plugins.cat_fleet_chat_plugin import attachments as files
from plugins.cat_fleet_chat_plugin.hub_client import clamp_wait, request_json
from plugins.cat_fleet_chat_plugin.plugin_config import SETTINGS

logger = logging.getLogger("whiskers.cat_fleet_chat")
logger.debug("fleet chat tools imported")

_PLUGIN = "cat_fleet_chat_plugin"
_Shape = Annotated[dict[str, Any] | None, Field(description=SHAPE_PARAM_DESCRIPTION)]
_CHANNEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# Mirrors the hub's validate.CHANNEL_STATES. The hub is the authority; this
# only fails fast before a round trip.
CHANNEL_STATES = ("active", "paused", "blocked", "review", "done", "archived")


def _tool_error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": code, "message": message}


def _default_channel() -> str:
    return str(SETTINGS.get("default_channel") or "fleet")


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_post_message(
    channel: str,
    agent_name: str,
    text: str,
    reply_to: int | None = None,
    client_request_id: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Post a message. @handles at token boundaries become mentions.

    Retry with the same ``client_request_id`` and the same text to recover a
    lost response. A different payload with that key conflicts. Call this
    when you finish a step the rest of the fleet should see. To attach a
    file, use ``fleet_attach_file``; ``attachments`` here only passes
    through descriptors of files that tool already uploaded.
    """
    body: dict[str, Any] = {
        "channel": channel or _default_channel(),
        "author": agent_name,
        "text": text,
        "client_request_id": client_request_id,
    }
    if reply_to is not None:
        body["reply_to"] = reply_to
    if attachments:
        for descriptor in attachments:
            try:
                files.check_owned(descriptor)
            except files.AttachmentError as exc:
                return _tool_error(exc.code, str(exc))
        body["attachments"] = attachments
    return await request_json(
        "POST",
        "/api/v1/messages",
        body=body,
        tool_name="fleet_post_message",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_get_messages(
    channel: str,
    since_id: int | None = None,
    before_id: int | None = None,
    limit: int | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Read a channel. Use ``since_id`` for new messages and ``before_id`` for older ones.

    Do not send both cursors. Advance the cursor only through rows you
    received, and drain ``has_more`` before waiting. Call this at the start
    of a task and after each step you finish.
    """
    return await request_json(
        "GET",
        "/api/v1/messages",
        params={
            "channel": channel or _default_channel(),
            "since_id": since_id,
            "before_id": before_id,
            "limit": limit,
        },
        tool_name="fleet_get_messages",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "wait"},
    annotations={"readOnlyHint": True},
)
async def fleet_wait_for_mentions(
    agent_name: str,
    since_id: int | None = 0,
    channel: str | None = None,
    timeout_seconds: int | None = None,
    limit: int | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Park until ``agent_name`` is mentioned after ``since_id``.

    Not for GOAP plans — a blocking wait would stall the planner. Use it
    from an interactive turn when you are idle. ``timeout_seconds`` is
    clamped to 1..300 (default 60). The HTTP client waits 10s longer than
    the hub so the hub's timeout wins. If your MCP client kills tools
    sooner than that (many default to 60s), pass a shorter
    ``timeout_seconds`` — at least 10 seconds under the client deadline.
    An empty result keeps your cursor and sets ``timed_out``.
    """
    seconds = clamp_wait(timeout_seconds)
    params: dict[str, Any] = {
        "agent": agent_name,
        "since_id": since_id if since_id is not None else 0,
        "timeout": seconds,
        "limit": limit,
    }
    if channel:
        params["channel"] = channel
    return await request_json(
        "GET",
        "/api/v1/wait",
        params=params,
        timeout=seconds + 10,
        tool_name="fleet_wait_for_mentions",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "wait"},
    annotations={"readOnlyHint": True},
)
async def fleet_wait_for_events(
    agent_name: str | None = None,
    after_event_id: int | None = 0,
    kinds: list[str] | None = None,
    channel: str | None = None,
    timeout_seconds: int | None = None,
    limit: int | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Park until an event relevant to ``agent_name`` lands after ``after_event_id``.

    Relevant: a message mentioning you (not your own), a task assigned to
    you that someone else changed, or any channel created, archived,
    unarchived, or changed state. Omit ``agent_name`` for every event.
    ``kinds`` narrows to ``message.created``, ``task.updated``,
    ``channel.created``, ``channel.archived``, ``channel.unarchived``,
    ``channel.state_changed``. Always send back the
    returned ``cursor``, even after ``timed_out``: it skips events that did
    not match. Not for GOAP plans; same timeout rules as
    ``fleet_wait_for_mentions`` (``timeout_seconds`` 1..300, HTTP +10s).
    """
    seconds = clamp_wait(timeout_seconds)
    params: dict[str, Any] = {
        "agent": agent_name or None,
        "after_event_id": after_event_id if after_event_id is not None else 0,
        "kinds": ",".join(kinds) if kinds else None,
        "channel": channel or None,
        "timeout": seconds,
        "limit": limit,
    }
    return await request_json(
        "GET",
        "/api/v1/notifications",
        params=params,
        timeout=seconds + 10,
        tool_name="fleet_wait_for_events",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_list_channels(
    include_archived: bool = False,
    state: list[str] | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """List shared channels with their lifecycle ``state``. A snapshot, not a cursor.

    Archived channels are hidden unless ``include_archived`` is true or
    ``state`` asks for them. ``state`` filters to any of active, paused,
    blocked, review, done, archived.
    """
    return await request_json(
        "GET",
        "/api/v1/channels",
        params={
            "include_archived": 1 if include_archived else None,
            "state": ",".join(state) if state else None,
        },
        tool_name="fleet_list_channels",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_set_channel_state(
    channel: str,
    state: str,
    agent_name: str,
    note: str | None = None,
    force: bool = False,
    _response_shape: _Shape = None,
) -> Any:
    """Set a channel's lifecycle state: active, paused, blocked, review, done, or archived.

    Use it so the fleet can see where a channel stands, and put the reason in
    ``note`` (e.g. what it is blocked on). Only ``archived`` changes behavior:
    the channel becomes read-only, and the change is refused with
    ``channel_has_open_tasks`` while tasks are unfinished unless ``force``
    cancels them. Any other state on an archived channel reopens it.
    Setting the current state is a no-op.
    """
    if not _CHANNEL_RE.fullmatch(channel or ""):
        return _tool_error("validation_error", "channel must match [a-z0-9][a-z0-9_-]{0,63}")
    if state not in CHANNEL_STATES:
        return _tool_error("validation_error", "state must be one of " + ", ".join(CHANNEL_STATES))
    return await request_json(
        "POST",
        f"/api/v1/channels/{channel}/state",
        body={"state": state, "actor": agent_name, "note": note or "", "force": bool(force)},
        tool_name="fleet_set_channel_state",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_archive_channel(
    channel: str,
    agent_name: str,
    note: str | None = None,
    force: bool = False,
    _response_shape: _Shape = None,
) -> Any:
    """Archive a finished channel. It stays readable but rejects posts and tasks.

    Refused with ``channel_has_open_tasks`` (listing ``task_ids``) while any
    task is not done or cancelled. Pass ``force=true`` only when those tasks
    should be cancelled. ``fleet`` cannot be archived. Repeating is a no-op.
    """
    if not _CHANNEL_RE.fullmatch(channel or ""):
        return _tool_error("validation_error", "channel must match [a-z0-9][a-z0-9_-]{0,63}")
    return await request_json(
        "POST",
        f"/api/v1/channels/{channel}/archive",
        body={"actor": agent_name, "note": note or "", "force": bool(force)},
        tool_name="fleet_archive_channel",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_unarchive_channel(
    channel: str,
    agent_name: str,
    state: str = "active",
    _response_shape: _Shape = None,
) -> Any:
    """Reopen an archived channel into ``state`` (default active). Cancelled tasks stay cancelled."""
    if not _CHANNEL_RE.fullmatch(channel or ""):
        return _tool_error("validation_error", "channel must match [a-z0-9][a-z0-9_-]{0,63}")
    return await request_json(
        "POST",
        f"/api/v1/channels/{channel}/unarchive",
        body={"actor": agent_name, "state": state},
        tool_name="fleet_unarchive_channel",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False},
)
async def fleet_attach_file(
    channel: str,
    agent_name: str,
    filename: str,
    content_text: str | None = None,
    content_base64: str | None = None,
    content_type: str | None = None,
    text: str | None = None,
    reply_to: int | None = None,
    client_request_id: str | None = None,
) -> Any:
    """Upload a file to the fleet attachment bucket and post it as a message.

    Pass exactly one of ``content_text`` (UTF-8) or ``content_base64``. The
    size cap comes from the plugin manifest (default 10 MB). ``text`` is the
    message body (defaults to "attached <filename>"); its @handles become
    mentions. Returns the hub message; ``attachments[0].id`` is what
    ``fleet_get_attachment`` takes. A retry with the same
    ``client_request_id`` and the same file reuses the object key so the hub
    can replay the original message. A failed hub post deletes the object.
    """
    if not _CHANNEL_RE.fullmatch(channel or ""):
        return _tool_error("validation_error", "channel must match [a-z0-9][a-z0-9_-]{0,63}")
    try:
        data = files.decode_content(content_text, content_base64)
        object_key = (
            files.stable_object_key(channel, filename, data, client_request_id)
            if client_request_id
            else None
        )
        descriptor = await files.upload(
            channel, filename, data, content_type, object_key=object_key
        )
    except files.AttachmentError as exc:
        return _tool_error(exc.code, str(exc))
    except Exception:
        logger.exception("fleet_attach_file: upload failed")
        return _tool_error("storage_error", "attachment storage is unavailable")
    body: dict[str, Any] = {
        "channel": channel,
        "author": agent_name,
        "text": text or f"attached {descriptor['filename']}",
        "client_request_id": client_request_id,
        "attachments": [descriptor],
    }
    if reply_to is not None:
        body["reply_to"] = reply_to
    try:
        result = await request_json(
            "POST",
            "/api/v1/messages",
            body=body,
            tool_name="fleet_attach_file",
        )
    except Exception:
        await files.delete(descriptor)
        logger.exception("fleet_attach_file: hub post failed")
        return _tool_error("api_error", "hub request failed")
    if isinstance(result, dict) and result.get("status") == "error":
        await files.delete(descriptor)
    return result


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_get_attachment(
    attachment_id: int,
    include_content: bool = False,
) -> Any:
    """Attachment metadata plus a short-lived ``presigned_url``.

    With ``include_content``, files under the inline cap (default 256 KB)
    also return ``content_text`` (text types) or ``content_base64``. Only
    files in the plugin attachment bucket are served; others are refused.
    """
    found = await request_json(
        "GET",
        f"/api/v1/attachments/{int(attachment_id)}",
        tool_name="fleet_get_attachment",
        raw=True,
    )
    if not isinstance(found, dict) or found.get("status") == "error":
        return found
    descriptor = found.get("attachment")
    if not isinstance(descriptor, dict):
        return _tool_error("api_error", "hub returned no attachment")
    try:
        return {"attachment": await files.describe(descriptor, include_content)}
    except files.AttachmentError as exc:
        return _tool_error(exc.code, str(exc))
    except Exception:
        logger.exception("fleet_get_attachment: storage failed")
        return _tool_error("storage_error", "attachment storage is unavailable")


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False},
)
async def fleet_create_channel(
    name: str,
    topic: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Create a shared channel. Duplicate names conflict and name the existing one.

    Names are lowercase slugs. Posting does not create a missing channel.
    """
    return await request_json(
        "POST",
        "/api/v1/channels",
        body={"name": name, "topic": topic or ""},
        tool_name="fleet_create_channel",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_create_task(
    channel: str,
    title: str,
    description: str | None = None,
    assignee: str | None = None,
    agent_name: str = "",
    client_request_id: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Create a task. Unassigned starts ``open``; an assignee starts ``claimed``.

    ``agent_name`` is the actor on the history row. Retry with the same
    ``client_request_id`` and the same payload.
    """
    return await request_json(
        "POST",
        "/api/v1/tasks",
        body={
            "channel": channel or _default_channel(),
            "title": title,
            "description": description or "",
            "assignee": assignee,
            "actor": agent_name or assignee,
            "client_request_id": client_request_id,
        },
        tool_name="fleet_create_task",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_claim_task(
    task_id: int,
    agent_name: str,
    expected_version: int | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Claim an unassigned open task. The current claimant may repeat this.

    A second claimant gets a conflict. Pass ``expected_version`` when you
    have a copy of the row.
    """
    return await request_json(
        "POST",
        f"/api/v1/tasks/{int(task_id)}/claim",
        body={"actor": agent_name, "expected_version": expected_version},
        tool_name="fleet_claim_task",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False},
)
async def fleet_update_task_status(
    task_id: int,
    status: str,
    agent_name: str,
    expected_version: int,
    note: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Move a task. ``expected_version`` is required and a mismatch conflicts.

    Allowed: open→claimed|cancelled; claimed→in_progress|blocked|open|cancelled;
    in_progress→blocked|done|cancelled; blocked→in_progress|open|cancelled.
    ``done`` and ``cancelled`` are final. Returning to ``open`` clears the assignee.
    """
    return await request_json(
        "POST",
        f"/api/v1/tasks/{int(task_id)}/status",
        body={
            "status": status,
            "actor": agent_name,
            "expected_version": expected_version,
            "note": note or "",
        },
        tool_name="fleet_update_task_status",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_list_tasks(
    channel: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Snapshot of tasks. An entity id cannot express edits to an existing task."""
    return await request_json(
        "GET",
        "/api/v1/tasks",
        params={"channel": channel, "status": status, "assignee": assignee},
        tool_name="fleet_list_tasks",
        shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_list_agents(_response_shape: _Shape = None) -> Any:
    """Handles seen on messages or task history, with last activity.

    This is recent activity, not presence. A row does not mean a CLI is running.
    """
    return await request_json(
        "GET", "/api/v1/agents", tool_name="fleet_list_agents", shape=_response_shape
    )
