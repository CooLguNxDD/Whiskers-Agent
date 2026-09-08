"""



Playground REST routes — bridge the admin UI to the dynamic graph orchestrator.

Endpoints
---------
POST   /api/playground/session_gated/chat                                    — Run a natural-language message through the dynamic graph; return its response envelope.
GET    /api/playground/session_gated/chat/sessions                           — List all chat sessions, newest first.
POST   /api/playground/session_gated/chat/sessions                           — Create a new chat session. Body: {title: string}.
GET    /api/playground/session_gated/chat/sessions/{session_id}              — Get a chat session with all its messages.
GET    /api/playground/session_gated/chat/sessions/{session_id}/executions   — Retrieve all workflow executions for a specific session.
POST   /api/playground/session_gated/chat/sessions/{session_id}/messages     — Append a message to a chat session.
POST   /api/playground/session_gated/chat_llm                                — Stream a plain (graph-less) chat completion from the active LLM.
GET    /api/playground/session_gated/graph                                   — Get the compiled LangGraph's nodes and edges topology.
GET    /api/playground/session_gated/mcp-token                               — Mint a short-lived Layer-1 access token for the frontend MCP client.
POST   /api/playground/session_gated/stream_goap                             — Stream a natural-language message through the dynamic graph.
GET    /api/playground/session_gated/tools                                   — List every exposed MCP tool with its full input/output JSON schema.
POST   /api/playground/session_gated/tools/invoke                            — Invoke a single MCP tool with arbitrary arguments; errors are returned as data.
"""

import asyncio
import logging
import json
import time
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from utils.error_response import safe_error_response

from core.context import http_route_registry, mcp, _DB_AVAILABLE
from core.http_route_registry import AuthPolicy
from core_graph.mcp_tool import run_graph_impl, stream_graph_impl

logger = logging.getLogger("whiskers_agent")

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


async def _sse_keepalive_stream(agen, interval: float):
    """Wrap an async generator as SSE `data:` frames.

    Emits `: keepalive` comments when no item arrives within ``interval``,
    without losing an in-flight item across timeouts (the pending `__anext__`
    task survives multiple keepalive ticks).
    """
    pending = asyncio.create_task(agen.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval, return_when=asyncio.FIRST_COMPLETED)
            if pending not in done:
                yield ": keepalive\n\n"
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            yield f"data: {json.dumps(chunk)}\n\n"
            if chunk.get("type") in ("end", "error"):
                return
            pending = asyncio.create_task(agen.__anext__())
    finally:
        if not pending.done():
            pending.cancel()
            try:
                await pending
            except (asyncio.CancelledError, StopAsyncIteration):
                pass

# ---------------------------------------------------------------------------
# Cached registry mapping for tool -> plugin
# ---------------------------------------------------------------------------

_tool_to_plugin_cache: dict[str, str] = {}
_tool_to_plugin_lock: asyncio.Lock | None = None
_tool_to_plugin_cache_last_updated: float = 0.0
_CACHE_TTL = 60.0  # seconds

async def _get_tool_plugin_mapping() -> dict[str, str]:
    """Return a cached mapping of tool capability -> plugin_id."""
    global _tool_to_plugin_cache, _tool_to_plugin_lock, _tool_to_plugin_cache_last_updated

    if not _DB_AVAILABLE:
        return {}

    now = time.monotonic()
    if _tool_to_plugin_lock is None:
        _tool_to_plugin_lock = asyncio.Lock()

    # Fast path outside the lock; re-check inside (double-checked locking).
    if now - _tool_to_plugin_cache_last_updated < _CACHE_TTL and _tool_to_plugin_cache_last_updated > 0:
        return _tool_to_plugin_cache

    async with _tool_to_plugin_lock:
        now = time.monotonic()
        if now - _tool_to_plugin_cache_last_updated < _CACHE_TTL and _tool_to_plugin_cache_last_updated > 0:
            return _tool_to_plugin_cache

        new_mapping: dict[str, str] = {}
        try:
            from db_layer.plugin_registry_store import DBPluginRegistry
            for rec in await DBPluginRegistry().get_all():
                for cap in rec.capabilities or []:
                    new_mapping.setdefault(cap, rec.id)
            _tool_to_plugin_cache = new_mapping
            _tool_to_plugin_cache_last_updated = time.monotonic()
        except Exception as exc:
            logger.warning("Failed to update tool-to-plugin cache: %s", exc)

    return _tool_to_plugin_cache


async def _resolve_principal_role(request: Request) -> str | None:
    session_token = request.cookies.get("session")
    if not session_token:
        return None
    from core.context import oauth_provider
    if oauth_provider is None or oauth_provider._svc is None:
        return None
    try:
        payload = await oauth_provider._svc.validate_token(session_token)
        return payload.get("whiskers_role") or payload.get("ocat_role")
    except Exception as exc:
        logger.warning("_resolve_principal_role: validate_token failed: %s", exc)
        return None


@http_route_registry.route(
    route="playground",
    endpoint="chat",
    methods=["POST"],
    name="api_playground_chat",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def playground_chat(request: Request) -> Response:
    """Run a natural-language message through the dynamic graph; return its response envelope."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "missing_message"}, status_code=400)
    from core.api_key_management.scopes import playground_mcp_scopes, resolve_force_execute
    from core.scope_management import PrincipalKind
    principal_role = await _resolve_principal_role(request)
    # Session-gated: always authenticated; scopes authorize tools.
    force_execute = resolve_force_execute(body, principal_role, authenticated=True)
    session_id = body.get("session_id")
    scopes = playground_mcp_scopes(principal_role)
    result = await run_graph_impl(
        message,
        force_execute=force_execute,
        session_id=session_id,
        caller_scopes=scopes,
        caller_role=principal_role,
        caller_kind=PrincipalKind.SESSION_USER,
    )
    return JSONResponse(result)



@http_route_registry.route(
    route="playground",
    endpoint="mcp-token",
    methods=["GET"],
    name="api_playground_mcp_token",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def playground_mcp_token(request: Request) -> Response:
    """Mint a short-lived Layer-1 access token for the frontend MCP client.

    The browser holds only an HttpOnly admin-session cookie (no JS-readable
    token), but the ``/mcp`` endpoint is gated by FastMCP's RS256 Bearer auth.
    This route — itself behind ``SessionGateMiddleware`` (the ``/api/`` prefix is
    gated) — exchanges that session for a Bearer the MCP client sends to ``/mcp``.

    When OAuth is disabled (env-var credential mode), ``/mcp`` has no auth, so an
    empty sentinel token is returned and the client omits the header.
    """
    from core.context import OAUTH_ENABLED, MCP_SERVER_URL
    import core.context as _ctx

    # `resource` is the absolute MCP endpoint this token is valid for. The client
    # attaches the Bearer only when its target matches this resource (or is a
    # same-origin relative path), so the admin token is never leaked to an
    # arbitrary external MCP server selected via the server-URL override.
    resource = f"{(MCP_SERVER_URL or '').rstrip('/')}/mcp"

    if not OAUTH_ENABLED:
        return JSONResponse({"access_token": "", "token_type": "none", "expires_in": 0, "resource": resource})

    svc = getattr(_ctx, "_oauth_svc", None)
    if svc is None:
        return JSONResponse({"error": "oauth_unavailable"}, status_code=503)

    try:
        from core.api_key_management.scopes import playground_mcp_scopes
        role = await _resolve_principal_role(request)
        scopes = playground_mcp_scopes(role)
        client_id = "playground-mcp"
        await svc.ensure_internal_client(client_id, scopes=" ".join(scopes))
        # Resolve user id from session when present
        whiskers_user_id = None
        session_token = request.cookies.get("session")
        if session_token:
            try:
                payload = await svc.validate_token(session_token)
                whiskers_user_id = payload.get("sub") or payload.get("whiskers_user_id") or payload.get("ocat_user_id")
            except Exception:
                logger.debug("playground_routes.py: swallowed exception", exc_info=True)
        extra_claims = {"whiskers_role": role, "ocat_role": role} if role else {}
        if whiskers_user_id:
            extra_claims["whiskers_user_id"] = whiskers_user_id
            extra_claims["ocat_user_id"] = whiskers_user_id
        pair = await svc._issue_token_pair(
            client_id, scopes, extra_claims=extra_claims or None
        )
    except Exception:
        logger.exception("Failed to mint playground MCP access token")
        return JSONResponse({"error": "token_mint_failed"}, status_code=500)

    return JSONResponse({
        "access_token": pair["access_token"],
        "token_type": "bearer",
        "expires_in": pair["expires_in"],
        "resource": resource,
    })
@http_route_registry.route(
    route="playground",
    endpoint="graph",
    methods=["GET"],
    name="api_playground_graph",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def playground_graph(request: Request) -> Response:
    """Get the compiled LangGraph's nodes and edges topology."""
    try:
        from core_graph.mcp_tool import _get_graph, _LLM_USABLE, _DB_AVAILABLE
        if not _LLM_USABLE or not _DB_AVAILABLE:
            return JSONResponse({"available": False, "nodes": [], "edges": []})

        graph = await _get_graph()
        gg = graph.get_graph()

        nodes_list = []
        for node_id, node in gg.nodes.items():
            if node_id in ("__start__", "__end__"):
                continue
            nodes_list.append({
                "id": node.id,
                "name": node.name,
                "data_type": type(node.data).__name__ if node.data else None
            })

        edges_list = []
        for edge in gg.edges:
            if edge.source == "__start__" or edge.target == "__end__":
                continue
            edges_list.append({
                "source": edge.source,
                "target": edge.target,
                "conditional": edge.conditional,
            })

        return JSONResponse({"available": True, "nodes": nodes_list, "edges": edges_list})
    except Exception as exc:
        logger.exception("Failed to get playground graph topology")
        return JSONResponse({"available": False, "error": str(exc), "nodes": [], "edges": []})


@http_route_registry.route(
    route="playground",
    endpoint="stream_goap",
    methods=["POST"],
    name="api_playground_stream_goap",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def playground_stream_goap(request: Request) -> Response:
    """Stream a natural-language message through the dynamic graph."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "missing_message"}, status_code=400)
    from core.api_key_management.scopes import playground_mcp_scopes, resolve_force_execute
    from core.scope_management import PrincipalKind
    principal_role = await _resolve_principal_role(request)
    # Session-gated: always authenticated; scopes authorize tools.
    force_execute = resolve_force_execute(body, principal_role, authenticated=True)
    session_id = body.get("session_id")
    scopes = playground_mcp_scopes(principal_role)

    from utils.server_config import ELICIT_KEEPALIVE_INTERVAL_S
    # caller_source intentionally omitted (defaults to None): this route is a
    # human/cookie-gated entrypoint, never the GoapAgent CLI one-shot child's
    # own MCP loopback (that reaches this server over the injected bearer at
    # /mcp), so the one-shot recursion guard never applies here. No bearer_token
    # either — the one-shot injector mints its own short-lived admin token
    # (core_graph.goap_agent.cli_inject.mint_goap_agent_token) when unset.
    agen = stream_graph_impl(
        message,
        force_execute,
        session_id,
        caller_scopes=scopes,
        caller_role=principal_role,
        caller_kind=PrincipalKind.SESSION_USER,
    )
    stream = _sse_keepalive_stream(agen, float(ELICIT_KEEPALIVE_INTERVAL_S))
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)


@http_route_registry.route(
    route="playground",
    endpoint="chat_llm",
    methods=["POST"],
    name="api_playground_chat_llm",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def playground_chat_llm(request: Request) -> Response:
    """Stream a plain (graph-less) chat completion from the active LLM.

    Stateless: the client sends the full conversation as a messages array.
    Reuses the playground SSE envelope (token / end / error).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    role_map = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}
    raw_msgs = body.get("messages") or []
    lc_msgs = [
        role_map[m["role"]](content=m["content"])
        for m in raw_msgs
        if isinstance(m, dict) and m.get("role") in role_map and (m.get("content") or "").strip()
    ]
    if not lc_msgs:
        return JSONResponse({"error": "missing_messages"}, status_code=400)

    async def event_generator():
        """Asynchronously yield SSE data chunks for the plain LLM chat stream."""
        try:
            from core.llm_config_service import get_graph_llm

            llm = await get_graph_llm()
            async for chunk in llm.astream(lc_msgs):
                content = chunk.content
                if isinstance(content, list):
                    # Anthropic-style block list — keep only text blocks.
                    content = "".join(
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                if content:
                    yield f"data: {json.dumps({'type': 'token', 'node': 'llm', 'text': content})}\n\n"
            yield 'data: {"type": "end"}\n\n'
        except Exception as exc:
            logger.exception("playground chat_llm failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=_SSE_HEADERS)


@http_route_registry.route(
    route="playground",
    endpoint="tools",
    methods=["GET"],
    name="api_playground_tools",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def playground_tools(request: Request) -> Response:
    """List every exposed MCP tool with its full input/output JSON schema."""
    try:
        tools = await mcp.list_tools()
    except Exception as exc:
        return safe_error_response(exc, log_ctx="playground tools listing failed")

    name_to_plugin = await _get_tool_plugin_mapping()

    items = []
    for t in tools:
        tags = sorted(t.tags) if t.tags else []
        items.append(
            {
                "name": t.name,
                "plugin": name_to_plugin.get(t.name) or (tags[0] if tags else "core"),
                "description": t.description or "",
                "input_schema": t.parameters or {},
                "output_schema": t.output_schema or {},
                "enabled": True,
            }
        )
    return JSONResponse({"tools": items})


@http_route_registry.route(
    route="playground",
    endpoint="tools/invoke",
    methods=["POST"],
    name="api_playground_invoke_tool",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def playground_invoke_tool(request: Request) -> Response:
    """Invoke a single MCP tool with arbitrary arguments; errors are returned as data."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    tool_name = (body.get("tool") or "").strip()
    if not tool_name:
        return JSONResponse({"error": "missing_tool"}, status_code=400)
    arguments = body.get("arguments") or {}
    if not isinstance(arguments, dict):
        return JSONResponse({"error": "arguments_must_be_object"}, status_code=400)

    # Retrieve subject if authenticated
    subject = None
    try:
        session_token = request.cookies.get("session")
        if session_token:
            from core.context import oauth_provider
            if oauth_provider is not None and oauth_provider._svc is not None:
                payload = await oauth_provider._svc.validate_token(session_token)
                subject = payload.get("sub")
    except Exception:
        logger.debug("playground_routes.py: swallowed exception", exc_info=True)

    name_to_plugin = await _get_tool_plugin_mapping()
    plugin_id = name_to_plugin.get(tool_name, "core")

    # Scope gate: pre-check + set request principal so middleware enforces too
    from core.api_key_management.scopes import playground_mcp_scopes
    from core.scope_management import (
        PrincipalKind,
        ScopeGrant,
        evaluate_access,
        reset_request_principal,
        set_request_principal,
    )
    role = await _resolve_principal_role(request)
    scopes = playground_mcp_scopes(role)
    grant = ScopeGrant(scopes=scopes, role=role, kind=PrincipalKind.SESSION_USER)

    tags: tuple = ()
    resolved_plugin = plugin_id if plugin_id != "core" else ""
    try:
        from core.context._registries import route_registry
        desc = route_registry.get(tool_name) if route_registry else None
        if desc is not None:
            resolved_plugin = desc.plugin_id or resolved_plugin
            plugin_id = desc.plugin_id or plugin_id
            tags = tuple(desc.tags or ())
    except Exception:
        logger.debug("playground_routes.py: swallowed exception", exc_info=True)

    # Only pre-check when we can resolve a real plugin (non-empty required set)
    if resolved_plugin:
        decision = evaluate_access(
            grant,
            plugin_id=resolved_plugin,
            tags=tags,
            tool_name=tool_name,
            path="direct_tool",
        )
        if not decision.allowed:
            return JSONResponse(
                {"error": "scope_denied", "message": decision.reason, "tool": tool_name},
                status_code=403,
            )

    principal_token = set_request_principal(PrincipalKind.SESSION_USER, role, scopes)
    start = time.perf_counter()
    try:
        result = await mcp.call_tool(tool_name, arguments)
        ms = round((time.perf_counter() - start) * 1000)
        try:
            from core.telemetry import collector
            collector.record_tool_call(
                tool=tool_name,
                plugin_id=plugin_id,
                model=None,
                latency_ms=ms,
                ok=True,
                subject=subject
            )
        except Exception:
            logger.debug("playground_routes.py: swallowed exception", exc_info=True)

        return JSONResponse(
            {
                "ok": True,
                "tool": tool_name,
                "ms": ms,
                "structured_content": result.structured_content,
                "content": [c.model_dump(mode="json") for c in (result.content or [])],
                "error": None,
                "error_type": None,
            }
        )
    except Exception as exc:
        ms = round((time.perf_counter() - start) * 1000)
        logger.warning("playground invoke %s failed: %s", tool_name, exc)
        try:
            from core.telemetry import collector
            collector.record_tool_call(
                tool=tool_name,
                plugin_id=plugin_id,
                model=None,
                latency_ms=ms,
                ok=False,
                error_type=type(exc).__name__,
                subject=subject
            )
        except Exception:
            logger.debug("playground_routes.py: swallowed exception", exc_info=True)

        return JSONResponse(
            {
                "ok": False,
                "tool": tool_name,
                "ms": ms,
                "structured_content": None,
                "content": [],
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        )
    finally:
        reset_request_principal(principal_token)


# ---------------------------------------------------------------------------
# Chat session persistence (core_chat_001)
# ---------------------------------------------------------------------------

def _db_available() -> bool:
    """Returns True if DATABASE_URL is set and the chat store is usable."""
    import os
    return bool(os.environ.get("DATABASE_URL"))


@http_route_registry.route(
    route="playground",
    endpoint="chat/sessions",
    methods=["POST"],
    name="api_playground_create_chat_session",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def create_chat_session(request: Request) -> Response:
    """Create a new chat session. Body: {title: string}."""
    if not _db_available():
        return JSONResponse({"error": "database_unavailable"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    title = (body.get("title") or "New chat").strip()[:200]
    try:
        from db_layer.chat_store import create_session
        session_id = await create_session(title)
        return JSONResponse({"id": session_id, "title": title})
    except Exception as exc:
        return safe_error_response(exc, log_ctx="create_chat_session failed")


@http_route_registry.route(
    route="playground",
    endpoint="chat/sessions",
    methods=["GET"],
    name="api_playground_list_chat_sessions",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def list_chat_sessions(request: Request) -> Response:
    """List all chat sessions, newest first."""
    if not _db_available():
        return JSONResponse({"sessions": []})

    # Parse limit from query string, defaulting to 50
    limit_str = request.query_params.get("limit", "50")
    try:
        limit = int(limit_str)
        if limit < 0:
            limit = 50
        else:
            limit = min(limit, 100)  # Cap to prevent performance degradation
    except ValueError:
        limit = 50

    try:
        from db_layer.chat_store import list_sessions
        sessions = await list_sessions(limit=limit)
        return JSONResponse({"sessions": sessions})
    except Exception as exc:
        return safe_error_response(exc, log_ctx="list_chat_sessions failed")


@http_route_registry.route(
    route="playground",
    endpoint="chat/sessions/{session_id}",
    methods=["GET"],
    name="api_playground_get_chat_session",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def get_chat_session(request: Request) -> Response:
    """Get a chat session with all its messages."""
    if not _db_available():
        return JSONResponse({"error": "database_unavailable"}, status_code=503)
    session_id = request.path_params.get("session_id", "")
    try:
        from db_layer.chat_store import get_session
        data = await get_session(session_id)
        if data is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse(data)
    except Exception as exc:
        return safe_error_response(exc, log_ctx="get_chat_session failed")


@http_route_registry.route(
    route="playground",
    endpoint="chat/sessions/{session_id}/messages",
    methods=["POST"],
    name="api_playground_append_chat_message",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def append_chat_message(request: Request) -> Response:
    """Append a message to a chat session."""
    if not _db_available():
        return JSONResponse({"error": "database_unavailable"}, status_code=503)
    session_id = request.path_params.get("session_id", "")
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    role = (body.get("role") or "").strip()
    content = (body.get("content") or "").strip()
    if role not in ("user", "assistant") or not content:
        return JSONResponse({"error": "invalid_message"}, status_code=400)
    try:
        from db_layer.chat_store import append_message, touch_session
        msg_id = await append_message(
            session_id=session_id,
            role=role,
            content=content,
            engine=body.get("engine"),
            goap_state=body.get("goap_state"),
            raw=body.get("raw"),
        )
        await touch_session(session_id)
        return JSONResponse({"id": msg_id})
    except Exception as exc:
        return safe_error_response(exc, log_ctx="append_chat_message failed")


@http_route_registry.route(
    route="playground",
    endpoint="chat/sessions/{session_id}/executions",
    methods=["GET"],
    name="api_playground_list_executions",
    auth_policy=AuthPolicy.SESSION_GATED,
    owner="api.playground",
)
async def list_executions_route(request: Request) -> Response:
    """Retrieve all workflow executions for a specific session."""
    if not _db_available():
        return JSONResponse({"executions": []})
    session_id = request.path_params.get("session_id", "")
    try:
        from db_layer.execution_store import list_executions
        executions = await list_executions(session_id)
        return JSONResponse({"executions": executions})
    except Exception as exc:
        return safe_error_response(exc, log_ctx="list_executions_route failed")

