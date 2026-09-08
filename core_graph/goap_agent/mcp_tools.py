"""GoapAgent MCP tools — node surface + CLI agent runner (no HTTP routes).

Import side-effect registers tools on the core FastMCP instance and contributes
scope vocabulary tokens. Must never register playground/graph REST routes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from utils.error_response import tool_error
from core.context import mcp
from core_graph.goap_agent import node_runner
from core_graph.goap_agent.cli.runner import run_cli_agent_dict
from core_graph.goap_agent.session_store import get_session_store, reset_session_store_for_tests  # noqa: F401
from core_graph.goap_agent.state_codec import initial_state, merge_delta, to_public_snapshot

logger = logging.getLogger("whiskers_agent.goap_agent")

_TAG = "GoapAgent"
_TAGS = {_TAG}
_PLUGIN_ID = "GoapAgent"

# Per-session locks for compound get→mutate→update (SessionStore only locks
# individual ops; concurrent invoke/confirm/clarify can lose updates).
_goap_session_locks: dict[str, asyncio.Lock] = {}
_goap_session_locks_guard = asyncio.Lock()


@asynccontextmanager
async def _session_lock(session_id: str | None) -> AsyncIterator[None]:
    """Serialize compound session mutations for one session_id."""
    if not session_id:
        yield
        return
    async with _goap_session_locks_guard:
        if session_id not in _goap_session_locks:
            _goap_session_locks[session_id] = asyncio.Lock()
        lock = _goap_session_locks[session_id]
    async with lock:
        yield


def _drop_session_lock(session_id: str) -> None:
    """Best-effort remove a session lock after destroy (tests / cleanup)."""
    _goap_session_locks.pop(session_id, None)


def reset_goap_session_locks_for_tests() -> None:
    """Clear per-session locks (unit tests only)."""
    _goap_session_locks.clear()

# ---------------------------------------------------------------------------
# Scope vocabulary (MCP tools only — not routes)
# ---------------------------------------------------------------------------

def _register_scopes() -> None:
    try:
        from core.scope_management.registration import get_permission_registry

        get_permission_registry().register_plugin_permissions(
            _PLUGIN_ID,
            [
                {
                    "token": f"plugin:{_PLUGIN_ID}",
                    "description": "Full access to GoapAgent node/CLI tools",
                },
                {
                    "token": f"group:{_PLUGIN_ID}:read",
                    "description": "Read GoapAgent sessions and list nodes",
                    "access": "read",
                },
                {
                    "token": f"group:{_PLUGIN_ID}:write",
                    "description": "Invoke nodes, run CLI agents, mutate sessions",
                    "access": "write",
                },
            ],
            replace=False,
        )
    except Exception as exc:
        logger.debug("GoapAgent scope registration skipped: %s", exc)


_register_scopes()


def _caller_scopes() -> list[str] | None:
    try:
        from fastmcp.server.dependencies import get_access_token

        tok = get_access_token()
        if tok is None:
            return None
        return list(tok.scopes) if tok.scopes is not None else []
    except Exception as exc:
        logger.debug("GoapAgent caller scopes resolution failed: %s", exc)
        return None


def _resolve_mcp_principal() -> tuple[bool, str | None, Any]:
    from core.scope_management import PrincipalKind

    try:
        from fastmcp.server.dependencies import get_access_token

        tok = get_access_token()
    except Exception as exc:
        logger.debug("GoapAgent principal resolution failed: %s", exc)
        return False, None, None
    if tok is None:
        return False, None, None
    claims = getattr(tok, "claims", None)
    role = (claims.get("whiskers_role") or claims.get("ocat_role")) if isinstance(claims, dict) else None
    kind = PrincipalKind.OAUTH_CLIENT
    client_id = getattr(tok, "client_id", None) or (
        claims.get("client_id") if isinstance(claims, dict) else None
    )
    if isinstance(client_id, str) and (client_id.startswith("whiskers_") or client_id.startswith("octk_")):
        kind = PrincipalKind.API_KEY
    elif isinstance(claims, dict) and (claims.get("whiskers_user_id") or claims.get("ocat_user_id")):
        kind = PrincipalKind.SESSION_USER
    return True, role if isinstance(role, str) else None, kind


# ---------------------------------------------------------------------------
# Session + inventory tools
# ---------------------------------------------------------------------------

@mcp.tool(name="GoapAgent_list_nodes", tags=_TAGS, annotations={"readOnlyHint": True})
async def GoapAgent_list_nodes() -> dict[str, Any]:
    """List active LangGraph/GOAP node names from GRAPH_SPEC."""
    names = node_runner.list_node_names(resolve_when=True)
    return {"status": "ok", "nodes": names, "count": len(names)}


@mcp.tool(name="GoapAgent_create_session", tags=_TAGS)
async def GoapAgent_create_session(
    user_message: str,
    force_execute: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Create a GoapAgent session seeded for stepwise node invocation.

    Does not run the full graph — call GoapAgent_invoke_node or per-node tools
    (GoapAgent_turn_init, GoapAgent_triage, …) to advance.
    """
    if not user_message or not str(user_message).strip():
        return tool_error("missing_required_fields", "Please provide: user_message", missing_fields=["user_message"])

    authenticated, role, kind = _resolve_mcp_principal()
    scopes = _caller_scopes() if authenticated else None

    state = initial_state(
        user_message.strip(),
        force_execute=bool(force_execute),
        session_id=session_id,
        caller_scopes=scopes,
        caller_role=role,
        caller_kind=kind,
    )
    # Seed messages like run_graph when possible
    try:
        from langchain_core.messages import HumanMessage

        state["messages"] = [HumanMessage(content=user_message.strip())]
    except Exception:
        logger.debug("mcp_tools.py: swallowed exception", exc_info=True)

    store = await get_session_store()
    sess = await store.create(state, session_id=session_id)
    return {
        "status": "ok",
        "session_id": sess.session_id,
        "state": to_public_snapshot(sess.state),
        "next": "Call GoapAgent_turn_init then GoapAgent_triage, or GoapAgent_invoke_node.",
    }


@mcp.tool(name="GoapAgent_get_state", tags=_TAGS, annotations={"readOnlyHint": True})
async def GoapAgent_get_state(
    session_id: str,
    include_messages: bool = False,
) -> dict[str, Any]:
    """Return a JSON-safe snapshot of a GoapAgent session state."""
    if not session_id:
        return tool_error("missing_required_fields", "Please provide: session_id", missing_fields=["session_id"])
    store = await get_session_store()
    sess = await store.get(session_id)
    if sess is None:
        return tool_error("not_found", f"Unknown session_id: {session_id}")
    return {
        "status": "ok",
        "session_id": sess.session_id,
        "last_node": sess.last_node,
        "history": list(sess.history),
        "state": to_public_snapshot(sess.state, include_messages=include_messages),
    }


@mcp.tool(name="GoapAgent_destroy_session", tags=_TAGS)
async def GoapAgent_destroy_session(session_id: str) -> dict[str, Any]:
    """Drop a GoapAgent session from the in-memory store."""
    if not session_id:
        return tool_error("missing_required_fields", "Please provide: session_id", missing_fields=["session_id"])
    async with _session_lock(session_id):
        store = await get_session_store()
        ok = await store.destroy(session_id)
    _drop_session_lock(session_id)
    if not ok:
        return tool_error("not_found", f"Unknown session_id: {session_id}")
    return {"status": "ok", "session_id": session_id, "destroyed": True}


@mcp.tool(name="GoapAgent_invoke_node", tags=_TAGS)
async def GoapAgent_invoke_node(
    session_id: str,
    node_name: str,
) -> dict[str, Any]:
    """Invoke a single graph node by name against an existing session."""
    if not session_id or not node_name:
        missing = [k for k, v in (("session_id", session_id), ("node_name", node_name)) if not v]
        return tool_error("missing_required_fields", f"Please provide: {', '.join(missing)}", missing_fields=missing)

    async with _session_lock(session_id):
        store = await get_session_store()
        sess = await store.get(session_id)
        if sess is None:
            return tool_error("not_found", f"Unknown session_id: {session_id}")

        try:
            delta = await node_runner.invoke_node(node_name, sess.state)
        except KeyError as exc:
            return tool_error("unknown_node", str(exc), nodes=node_runner.list_node_names())
        except Exception as exc:
            logger.exception("GoapAgent_invoke_node failed: %s/%s", session_id, node_name)
            return tool_error("node_failed", str(exc), node_name=node_name)

        new_state = merge_delta(sess.state, delta)
        await store.update(session_id, new_state, last_node=node_name)
        return {
            "status": "ok",
            "session_id": session_id,
            "node_name": node_name,
            "delta": to_public_snapshot(delta),
            "state": to_public_snapshot(new_state),
            "response": new_state.get("response"),
        }


@mcp.tool(name="GoapAgent_submit_confirm", tags=_TAGS)
async def GoapAgent_submit_confirm(
    session_id: str,
    approved: bool = True,
) -> dict[str, Any]:
    """Apply a confirm-gate answer into session state (headless path).

    Sets force_execute when approved so the next context_check/builder path proceeds.
    """
    if not session_id:
        return tool_error("missing_required_fields", "Please provide: session_id", missing_fields=["session_id"])
    async with _session_lock(session_id):
        store = await get_session_store()
        sess = await store.get(session_id)
        if sess is None:
            return tool_error("not_found", f"Unknown session_id: {session_id}")
        state = dict(sess.state)
        if approved:
            state["force_execute"] = True
            state["gate_decision"] = "execute"
            state["response"] = None
        else:
            state["response"] = {
                "status": "cancelled",
                "message": "User declined confirmation",
            }
        await store.update(session_id, state, last_node="submit_confirm")
        return {
            "status": "ok",
            "session_id": session_id,
            "approved": approved,
            "state": to_public_snapshot(state),
            "next": "GoapAgent_context_check" if approved else None,
        }


@mcp.tool(name="GoapAgent_submit_clarify", tags=_TAGS)
async def GoapAgent_submit_clarify(
    session_id: str,
    answer: str | list[str],
) -> dict[str, Any]:
    """Fold a clarify answer into user_query for a replan cycle."""
    if not session_id:
        return tool_error("missing_required_fields", "Please provide: session_id", missing_fields=["session_id"])
    async with _session_lock(session_id):
        store = await get_session_store()
        sess = await store.get(session_id)
        if sess is None:
            return tool_error("not_found", f"Unknown session_id: {session_id}")
        state = dict(sess.state)
        if isinstance(answer, list):
            answer_text = ", ".join(str(a) for a in answer)
        else:
            answer_text = str(answer)
        base = state.get("user_query") or ""
        state["user_query"] = f"{base}\n\n[clarification]: {answer_text}".strip()
        state["response"] = None
        state["clarification_question"] = None
        await store.update(session_id, state, last_node="submit_clarify")
        return {
            "status": "ok",
            "session_id": session_id,
            "state": to_public_snapshot(state),
            "next": "Re-run planning nodes (decompose/embedder/planner).",
        }


@mcp.tool(name="GoapAgent_run_cli_agent", tags=_TAGS)
async def GoapAgent_run_cli_agent(
    prompt: str,
    agent: str | None = None,
    workdir: str | None = None,
    timeout_s: float | None = None,
    session_id: str | None = None,
    model: str | None = None,
    allowed_tools: list[str] | None = None,
    inject_mcp: bool | None = None,
) -> dict[str, Any]:
    """Spawn a headless CLI agent (claude / agy / generic) as a subprocess.

    Auto-injects package instructions (``instructions/CLI_AGENT.md``) and a
    temporary Claude ``--mcp-config`` pointing at this Whiskers Agent server so the
    child can call ``GoapAgent_*`` tools. Disable with ``inject_mcp=false`` or
    env ``GOAP_AGENT_AUTO_MCP=0``. Binary must be on PATH
    (``CLAUDE_CLI_BINARY`` / ``AGY_CLI_BINARY``).

    Process logging (default on): writes ``logs/goap_agent/<run_id>/`` with
    ``process.log`` (lifecycle + internal stdout/stderr), ``events.jsonl``,
    ``stdout.txt`` / ``stderr.txt``, and ``run.json``. Paths returned in
    ``result.log``. Lines also mirror to the ``whiskers_agent.goap_agent.cli`` logger.
    """
    if not prompt or not str(prompt).strip():
        return tool_error("missing_required_fields", "Please provide: prompt", missing_fields=["prompt"])

    # Prefer the outer caller's bearer so the child inherits scopes when present.
    bearer: str | None = None
    try:
        from fastmcp.server.dependencies import get_access_token

        tok = get_access_token()
        if tok is not None:
            raw = getattr(tok, "token", None) or getattr(tok, "access_token", None)
            if isinstance(raw, str) and raw.strip():
                bearer = raw.strip()
    except Exception:
        bearer = None

    result = await run_cli_agent_dict(
        prompt.strip(),
        agent=agent,
        workdir=workdir,
        timeout_s=timeout_s,
        allowed_tools=allowed_tools,
        model=model,
        session_id=session_id,
        inject_mcp=inject_mcp,
        bearer_token=bearer,
    )
    return result


@mcp.tool(name="GoapAgent_list_cli_drivers", tags=_TAGS, annotations={"readOnlyHint": True})
async def GoapAgent_list_cli_drivers() -> dict[str, Any]:
    """List registered CLI agent drivers and whether each binary is available."""
    from core_graph.goap_agent.cli.registry import get_driver, list_drivers

    drivers = []
    for name in list_drivers():
        d = get_driver(name)
        drivers.append({
            "name": name,
            "binary": d.binary(),
            "available": d.available(),
        })
    return {"status": "ok", "drivers": drivers}


# ---------------------------------------------------------------------------
# Per-node thin wrappers (generated from GRAPH_SPEC names)
# ---------------------------------------------------------------------------

_NODE_TOOL_NAMES = [
    "turn_init",
    "triage",
    "chat_node",
    "decompose",
    "embedder",
    "planner",
    "context_check",
    "step_resolver",
    "confirm_node",
    "clarify_node",
    "permission_gate",
    "builder",
    "wait_node",
    "executor",
    "validator",
    "retry_node",
    "step_dispatcher",
    "summary_node",
    "goap_goal",
]


def _make_node_tool(node_name: str):
    """Build an MCP tool coroutine that invokes one fixed node."""

    async def _tool(session_id: str) -> dict[str, Any]:
        return await GoapAgent_invoke_node(session_id=session_id, node_name=node_name)

    _tool.__name__ = f"GoapAgent_{node_name}"
    _tool.__doc__ = (
        f"Invoke the ``{node_name}`` graph node on a GoapAgent session. "
        f"Equivalent to GoapAgent_invoke_node(session_id, node_name={node_name!r})."
    )
    return _tool


for _n in _NODE_TOOL_NAMES:
    _fn = _make_node_tool(_n)
    mcp.tool(name=f"GoapAgent_{_n}", tags=_TAGS)(_fn)


logger.info(
    "GoapAgent MCP tools registered (%d node wrappers + session/cli tools)",
    len(_NODE_TOOL_NAMES),
)
