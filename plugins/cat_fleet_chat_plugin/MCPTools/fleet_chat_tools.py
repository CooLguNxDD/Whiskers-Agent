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
_AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# Hub pub/sub topic: dotted lowercase segments, no wildcards (it is a path segment).
_TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}(\.[a-z0-9][a-z0-9_-]{0,63}){0,7}$")
_PROPOSAL_ID_RE = re.compile(r"^[Pp]?[0-9]+$")
REACTION_KINDS = ("+1", "-1", "heart", "laugh", "eyes", "tada", "rocket", "thinking")


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
    you that someone else changed, a publish to a topic you subscribed to
    (``fleet_subscribe``), or any channel created, archived, unarchived, or
    changed state. Omit ``agent_name`` for every event.
    ``kinds`` narrows to e.g. ``message.created``, ``task.updated``,
    ``topic.published``, ``channel.created``, ``channel.archived``,
    ``channel.unarchived``, ``channel.state_changed``. Always send back the
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
    can replay the original message. A definite hub rejection (4xx) deletes
    the object; an uncertain failure (timeout, 5xx) keeps it, since the hub
    may already have saved a message that references it.
    """
    if not _CHANNEL_RE.fullmatch(channel or ""):
        return _tool_error("validation_error", "channel must match [a-z0-9][a-z0-9_-]{0,63}")
    message_text = text or f"attached {files.safe_filename(filename)}"
    try:
        data = files.decode_content(content_text, content_base64)
        object_key = (
            files.stable_object_key(
                channel,
                filename,
                data,
                client_request_id,
                author=agent_name,
                text=message_text,
                reply_to=reply_to,
            )
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
        "text": message_text,
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
        # Outcome unknown: the hub may have saved the message, so keep the object.
        logger.exception("fleet_attach_file: hub post failed")
        return _tool_error("api_error", "hub request failed")
    if _hub_rejected(result):
        await files.delete(descriptor)
    return result


def _hub_rejected(result: Any) -> bool:
    """True only when the hub definitely did not save the message (4xx, or never sent)."""
    if not (isinstance(result, dict) and result.get("status") == "error"):
        return False
    if result.get("error") == "unsafe_url":
        return True
    status = result.get("http_status")
    return isinstance(status, int) and 400 <= status < 500


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


# V2 bodies omit absent fields, but retain empty lists/strings (proposal ref clears).
def _present(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


async def _mail_request(
    method: str,
    agent: str,
    suffix: str = "",
    **kwargs: Any,
) -> Any:
    # A handle is a single path segment, never a caller-supplied URL or route.
    if not _AGENT_RE.fullmatch(agent):
        return _tool_error("validation_error", "agent must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    return await request_json(method, f"/api/v1/mailbox/{agent}{suffix}", **kwargs)


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def fleet_mail_send(
    sender: str,
    recipient: str,
    body: str,
    subject: str | None = None,
    reply_to: int | None = None,
    client_request_id: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Send direct mail. Reuse client_request_id on retries; reply_to is a mail id."""
    return await request_json(
        "POST", "/api/v1/mailbox",
        body=_present(sender=sender, recipient=recipient, body=body, subject=subject,
                      reply_to=reply_to, client_request_id=client_request_id),
        tool_name="fleet_mail_send", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_mail_list(
    agent: str,
    state: str = "unread",
    since_id: int | None = 0,
    limit: int | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """List unread/read/all mail without marking read. Drain has_more using cursor as since_id; counts via fleet_mail_counts."""
    return await _mail_request(
        "GET", agent, params={"state": state, "since_id": since_id, "limit": limit},
        tool_name="fleet_mail_list", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_mail_read(
    agent: str,
    ids: list[int] | None = None,
    up_to_id: int | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Mark mail read using ids OR up_to_id. Safe to repeat."""
    return await _mail_request(
        "POST", agent, "/read", body=_present(ids=ids, up_to_id=up_to_id),
        tool_name="fleet_mail_read", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_mail_ack(
    agent: str,
    ids: list[int],
    _response_shape: _Shape = None,
) -> Any:
    """Acknowledge mail you acted on; ack also marks it read. Safe to repeat."""
    return await _mail_request(
        "POST", agent, "/ack", body={"ids": ids},
        tool_name="fleet_mail_ack", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_mail_counts(agent: str, _response_shape: _Shape = None) -> Any:
    """Read a handle's mailbox counts without listing mail."""
    return await _mail_request(
        "GET", agent, "/counts", tool_name="fleet_mail_counts", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def fleet_publish(
    publisher: str,
    topic: str,
    data: Any = None,
    client_request_id: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Publish JSON ``data`` to a dotted topic (``ci.whiskers.done``); wakes matching subscribers.

    Result lists ``subscribers``. Reuse client_request_id on retries.
    """
    if not _TOPIC_RE.fullmatch(topic):
        return _tool_error("validation_error", "topic must be dotted [a-z0-9][a-z0-9_-]{0,63} segments")
    return await request_json(
        "POST", f"/api/v1/topics/{topic}/publish",
        body=_present(publisher=publisher, data=data, client_request_id=client_request_id),
        tool_name="fleet_publish", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_subscribe(agent: str, pattern: str, _response_shape: _Shape = None) -> Any:
    """Subscribe to a topic pattern (``*`` = one segment, trailing ``>`` = the rest). Safe to repeat.

    Matching publishes then arrive as ``topic.published`` in ``fleet_wait_for_events``.
    """
    if not _AGENT_RE.fullmatch(agent):
        return _tool_error("validation_error", "agent must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    return await request_json(
        "POST", f"/api/v1/subscriptions/{agent}", body={"pattern": pattern},
        tool_name="fleet_subscribe", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_unsubscribe(agent: str, pattern: str, _response_shape: _Shape = None) -> Any:
    """Drop one exact subscription pattern. Safe to repeat."""
    if not _AGENT_RE.fullmatch(agent):
        return _tool_error("validation_error", "agent must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    return await request_json(
        "DELETE", f"/api/v1/subscriptions/{agent}", params={"pattern": pattern},
        tool_name="fleet_unsubscribe", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_list_subscriptions(
    agent: str | None = None,
    topic: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """List subscriptions: all, one agent's, or those matching a concrete topic."""
    return await request_json(
        "GET", "/api/v1/subscriptions", params=_present(agent=agent, topic=topic),
        tool_name="fleet_list_subscriptions", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_list_publications(
    pattern: str | None = None,
    since_id: int | None = 0,
    limit: int | None = None,
    order: str = "asc",
    before_id: int | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Replay topic publication history. asc: oldest first, cursor -> since_id; desc: newest first, cursor -> before_id."""
    return await request_json(
        "GET", "/api/v1/publications",
        params={"pattern": pattern, "since_id": since_id, "limit": limit,
                "order": order, "before_id": before_id},
        tool_name="fleet_list_publications", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def fleet_feed_post(
    author: str,
    body: str,
    quote_of: int | None = None,
    client_request_id: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Post to the feed, optionally quoting a post. Reuse client_request_id on retries."""
    return await request_json(
        "POST", "/api/v1/feed/posts",
        body=_present(author=author, body=body, quote_of=quote_of, client_request_id=client_request_id),
        tool_name="fleet_feed_post", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def fleet_feed_reply(
    author: str,
    parent_id: int,
    body: str,
    client_request_id: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Reply under parent_id in a feed thread. Reuse client_request_id on retries."""
    return await request_json(
        "POST", "/api/v1/feed/posts",
        body=_present(author=author, parent_id=parent_id, body=body, client_request_id=client_request_id),
        tool_name="fleet_feed_reply", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_feed_repost(author: str, post_id: int, _response_shape: _Shape = None) -> Any:
    """Repost a feed post; repeating it is a no-op."""
    return await request_json(
        "POST", f"/api/v1/feed/posts/{post_id}/repost", body={"author": author},
        tool_name="fleet_feed_repost", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_feed_react(
    handle: str, post_id: int, kind: str, _response_shape: _Shape = None,
) -> Any:
    """React: +1, -1, heart, laugh, eyes, tada, rocket, thinking. Votes replace each other; emoji are independent."""
    if kind not in REACTION_KINDS:
        return _tool_error("validation_error", "kind must be one of " + ", ".join(REACTION_KINDS))
    return await request_json(
        "PUT", f"/api/v1/feed/posts/{post_id}/reactions/{kind}", body={"handle": handle},
        tool_name="fleet_feed_react", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_feed_unreact(
    handle: str, post_id: int, kind: str, _response_shape: _Shape = None,
) -> Any:
    """Remove one of your reactions. Safe to repeat; same kinds as fleet_feed_react."""
    if kind not in REACTION_KINDS:
        return _tool_error("validation_error", "kind must be one of " + ", ".join(REACTION_KINDS))
    # Unlike PUT, the hub's DELETE handler reads the handle only from the query.
    return await request_json(
        "DELETE", f"/api/v1/feed/posts/{post_id}/reactions/{kind}", params={"handle": handle},
        tool_name="fleet_feed_unreact", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_feed_timeline(
    scope: str = "global",
    handle: str | None = None,
    tag: str | None = None,
    author: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Read global/following/hashtag/author feed; pass next_cursor as cursor. Use fleet_feed_thread for threads."""
    return await request_json(
        "GET", "/api/v1/feed/timeline",
        params={"scope": scope, "handle": handle, "tag": tag, "author": author, "cursor": cursor, "limit": limit},
        tool_name="fleet_feed_timeline", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_feed_thread(
    post_id: int,
    cursor: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Read a reply tree around post_id. Page with next_cursor; open more_replies by that post's id."""
    return await request_json(
        "GET", f"/api/v1/feed/posts/{post_id}", params={"thread": 1, "cursor": cursor},
        tool_name="fleet_feed_thread", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_knowledge_add(
    project: str,
    fact: str,
    author: str,
    topic: str | None = None,
    source_run: str | None = None,
    source_step: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Record a project fact (topic defaults to general). The hub deduplicates equivalent facts."""
    return await request_json(
        "POST", "/api/v1/knowledge/facts",
        body=_present(project=project, fact=fact, author=author, topic=topic,
                      source_run=source_run, source_step=source_step),
        tool_name="fleet_knowledge_add", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_knowledge_list(
    project: str | None = None,
    topic: str | None = None,
    status: str | None = None,
    author: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """List facts by project/topic/status/author, newest first. Pass next_cursor as cursor."""
    return await request_json(
        "GET", "/api/v1/knowledge/facts",
        params={"project": project, "topic": topic, "status": status, "author": author, "cursor": cursor, "limit": limit},
        tool_name="fleet_knowledge_list", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def fleet_knowledge_set_status(
    actor: str, fact_id: int, status: str, _response_shape: _Shape = None,
) -> Any:
    """Mark a fact active, superseded or rejected. Only active facts are searchable."""
    return await request_json(
        "PATCH", f"/api/v1/knowledge/facts/{fact_id}", body={"actor": actor, "status": status},
        tool_name="fleet_knowledge_set_status", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_knowledge_topics(
    project: str | None = None, _response_shape: _Shape = None,
) -> Any:
    """List knowledge topics, optionally for one project."""
    return await request_json(
        "GET", "/api/v1/knowledge/topics", params={"project": project},
        tool_name="fleet_knowledge_topics", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_list_proposals(
    project: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """List proposals by project/status, newest first. Pass next_cursor as cursor."""
    return await request_json(
        "GET", "/api/v1/proposals",
        params={"project": project, "status": status, "cursor": cursor, "limit": limit},
        tool_name="fleet_list_proposals", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_get_proposal(proposal_id: str | int, _response_shape: _Shape = None) -> Any:
    """Read one proposal by id, e.g. P12 or 12."""
    if not _PROPOSAL_ID_RE.fullmatch(str(proposal_id)):
        return _tool_error("validation_error", "proposal_id must be a number or P<number>")
    return await request_json(
        "GET", f"/api/v1/proposals/{proposal_id}",
        tool_name="fleet_get_proposal", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def fleet_create_proposal(
    author: str,
    project: str,
    title: str,
    summary: str,
    weight: int,
    evidence: list[str] | None = None,
    run_ref: str | None = None,
    step_ref: str | None = None,
    pr_ref: str | None = None,
    client_request_id: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Propose work (weight 1..10); discuss its returned post_id via feed replies. Reuse client_request_id on retries."""
    return await request_json(
        "POST", "/api/v1/proposals",
        body=_present(author=author, project=project, title=title, summary=summary, weight=weight,
                      evidence=evidence, run_ref=run_ref, step_ref=step_ref, pr_ref=pr_ref,
                      client_request_id=client_request_id),
        tool_name="fleet_create_proposal", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def fleet_update_proposal(
    proposal_id: str | int,
    actor: str,
    status: str | None = None,
    reason: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    evidence: list[str] | None = None,
    weight: int | None = None,
    run_ref: str | None = None,
    step_ref: str | None = None,
    pr_ref: str | None = None,
    _response_shape: _Shape = None,
) -> Any:
    """Edit fields/status; omitted fields stay, empty refs clear. Rejecting needs reason; the hub enforces transitions."""
    if not _PROPOSAL_ID_RE.fullmatch(str(proposal_id)):
        return _tool_error("validation_error", "proposal_id must be a number or P<number>")
    return await request_json(
        "PATCH", f"/api/v1/proposals/{proposal_id}",
        body=_present(actor=actor, status=status, reason=reason, title=title, summary=summary,
                      evidence=evidence, weight=weight, run_ref=run_ref, step_ref=step_ref, pr_ref=pr_ref),
        tool_name="fleet_update_proposal", shape=_response_shape,
    )


@mcp.tool(
    tags={_PLUGIN, "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def fleet_get_evolve_state(_response_shape: _Shape = None) -> Any:
    """Read evolve's paused/by/at state. Only allowlisted humans can pause or resume it."""
    return await request_json(
        "GET", "/api/v1/evolve/state", tool_name="fleet_get_evolve_state", shape=_response_shape,
    )
