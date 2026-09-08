"""
Core graph MCP tool that wraps the dynamic LangGraph orchestrator — ``run_graph``.

Routes any natural-language request through the dynamic graph (embedder →
planner → fast-path / builder + executor → validator → step_dispatcher).
The planner can compose **multiple tools per turn** ("super-agent" mode):
one prompt → planner picks 1-N tools → graph threads outputs between steps.

Configured via env vars:
  LLM_PROVIDER  – "openai" (default), "anthropic", or "gemini"
  LLM_MODEL     – model name override
  OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY
  DATABASE_URL  – REQUIRED (pgvector route embeddings)
  PLUGIN_API_URL – optional base URL for dynamic HTTP routes (plugin-specific)

Runs **headless**: confirm/clarify responses come back to the MCP client.
"""

import os
import json
import logging
import asyncio
import contextlib
import time
from typing import Any

from fastmcp import Context
from fastmcp.dependencies import CurrentContext
from core.context import mcp
from core.llm_provider_management import _LLM_AVAILABLE

logger = logging.getLogger("whiskers")

_DB_AVAILABLE = bool(os.environ.get("DATABASE_URL", "").strip())

# An LLM is usable if env vars provide one, OR the DB is available (the dynamic
# pool may hold an API token even when no env key is set).
_LLM_USABLE = _LLM_AVAILABLE or _DB_AVAILABLE

if not _LLM_AVAILABLE:
    logger.warning(
        "run_graph tool DISABLED – no LLM API key found. "
        "Set OPENAI_API_KEY, ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic, "
        "or GOOGLE_API_KEY + LLM_PROVIDER=gemini in your .env file to enable it."
    )
if not _DB_AVAILABLE:
    logger.warning(
        "run_graph tool DISABLED – DATABASE_URL not set. "
        "The dynamic graph requires Postgres + pgvector for route embeddings."
    )

# ---------------------------------------------------------------------------
# Lazy-initialised graph singleton & session locks — owned by runtime.bootstrap
# ---------------------------------------------------------------------------
import core_graph.runtime.bootstrap as _rt_bootstrap
from core_graph.runtime.bootstrap import (
    get_compiled_graph as _get_graph,
    invalidate_graph as _invalidate_graph_bootstrap,
    session_lock as _session_lock,
    thread_config as _thread_config,
)

# Back-compat module globals — unit tests monkeypatch these on mcp_tool.
# Production paths prefer bootstrap; eviction/shutdown/invalidate sync both.
_pg_pool = None
_pg_saver = None
_compiled_graph = None


def invalidate_graph() -> None:
    """Drop compiled-graph cache on bootstrap and back-compat mcp_tool globals."""
    global _compiled_graph
    _invalidate_graph_bootstrap()
    _compiled_graph = None


async def _evict_ephemeral_thread(thread_id: str | None) -> None:
    """Delete checkpoint state for ephemeral threads (uses patchable ``_pg_saver``)."""
    if not thread_id or not str(thread_id).startswith("ephemeral-"):
        return
    saver = _pg_saver if _pg_saver is not None else _rt_bootstrap._pg_saver
    if saver is None:
        return
    try:
        await saver.adelete_thread(thread_id)
    except Exception as exc:
        logger.debug("ephemeral thread cleanup skipped for %s: %s", thread_id, exc)


async def shutdown_checkpointer() -> None:
    """Close checkpointer pool and reset bootstrap + local back-compat globals."""
    global _pg_pool, _pg_saver, _compiled_graph
    # Prefer local pool when tests injected one onto this module.
    pool = _pg_pool if _pg_pool is not None else _rt_bootstrap._pg_pool
    if pool is not None:
        try:
            await pool.close()
        except Exception as exc:
            logger.warning(
                "Error during Postgres checkpointer pool shutdown: %s",
                exc,
                exc_info=True,
            )
    _rt_bootstrap._pg_pool = None
    _rt_bootstrap._pg_saver = None
    _rt_bootstrap._compiled_graph = None
    _pg_pool = None
    _pg_saver = None
    _compiled_graph = None


# Sentinel: distinguish omitted caller_scopes (read from token) from explicit None (CLI).
_UNSET = object()


def _get_access_token():
    """Return FastMCP access token or None when unauthenticated / unavailable."""
    try:
        from fastmcp.server.dependencies import get_access_token
        return get_access_token()
    except Exception as exc:
        logger.debug("access token unavailable: %s", exc)
        return None


def _caller_scopes() -> list[str] | None:
    """Resolve the current MCP caller's granted scopes, or None outside an
    authenticated request (e.g. stdio/local — scope checks are skipped)."""
    tok = _get_access_token()
    if tok is None:
        return None
    return list(tok.scopes) if tok.scopes is not None else []


def _resolve_caller_scopes(passed) -> list[str] | None:
    """Use explicit passed scopes when set; otherwise read from access token."""
    if passed is not _UNSET:
        return passed
    return _caller_scopes()


def _resolve_mcp_principal() -> tuple[bool, str | None, Any, str | None]:
    """Return (authenticated, ocat_role, PrincipalKind|None, ocat_source|None) from the MCP token.

    ``ocat_source`` is stamped on tokens minted for the GoapAgent CLI one-shot
    child (see core_graph.goap_agent.cli_inject.mint_goap_agent_token,
    extra_claims ocat_source="goap_agent_cli") — used as the one-shot
    recursion guard so a nested run_graph call from inside the CLI agent
    itself runs the native graph instead of spawning another subprocess.
    """
    from core.scope_management import PrincipalKind

    tok = _get_access_token()
    if tok is None:
        return False, None, None, None
    claims = getattr(tok, "claims", None)
    role = (claims.get("whiskers_role") or claims.get("ocat_role")) if isinstance(claims, dict) else None
    source = (claims.get("whiskers_source") or claims.get("ocat_source")) if isinstance(claims, dict) else None
    # Prefer explicit client_id / token shape; OAuth MCP tokens are the common path.
    kind = PrincipalKind.OAUTH_CLIENT
    client_id = getattr(tok, "client_id", None) or (
        claims.get("client_id") if isinstance(claims, dict) else None
    )
    if isinstance(client_id, str) and (client_id.startswith("whiskers_") or client_id.startswith("octk_")):
        kind = PrincipalKind.API_KEY
    elif isinstance(claims, dict) and claims.get("ocat_user_id"):
        # Playground-minted MCP bearer carries user claims
        kind = PrincipalKind.SESSION_USER
    return (
        True,
        role if isinstance(role, str) else None,
        kind,
        source if isinstance(source, str) else None,
    )


def _caller_bearer_token() -> str | None:
    """Return the raw bearer token string of the current MCP caller, or None."""
    tok = _get_access_token()
    if tok is None:
        return None
    raw = getattr(tok, "token", None) or getattr(tok, "access_token", None)
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _coerce_force_execute_arg(value) -> bool:
    """Normalize run_graph force_execute tool arg."""
    from core.scope_management import coerce_force_execute
    return coerce_force_execute(value)


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------

def _node_names_from_update_chunk(chunk: dict) -> list[str]:
    """Return graph node names from an updates chunk (skips __* internals)."""
    return [k for k in chunk if isinstance(k, str) and not k.startswith("__")]


def _update_chunks_from_protocol_event(evt: dict) -> list[dict]:
    """Extract update chunk dicts from a v3 protocol ``updates`` event."""
    params = evt.get("params") or {}
    data = params.get("data")
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


async def stream_graph_impl(
    user_message: str,
    force_execute: bool = False,
    session_id: str | None = None,
    caller_scopes=_UNSET,
    caller_role: str | None = None,
    caller_kind=None,
    caller_source: str | None = None,
    bearer_token: str | None = None,
):
    """Stream a natural-language request through the dynamic super-agent graph using astream.

    You are an internal tool executor.

    PARALLELISM RULE: When a request contains multiple INDEPENDENT, DISTINCT goals
    (goals that don't depend on each other's output), emit ALL tool calls in a single response.
    Do not wait for one to complete before calling the next.

    IMPORTANT SCOPE OF PARALLELISM RULE: A single operation repeated over a list
    (create N of X, do the same thing for each item in a set) is NOT multiple independent goals
    — it is ONE run_graph call, never N individual tool calls. Let the server fan out.

    Independent = no output from task A is needed as input to task B, AND the tasks are genuinely distinct goals.
    Sequential = task B needs task A's result first.

    OFFLOAD DIRECTIVE: For any multi-step, batch, or repeated request, call run_graph ONCE with
    the whole goal. run_graph does GOAP planning, fan-out, and replanning server-side. Do NOT loop
    the underlying plugin tools yourself. e.g. 'create 5 Jules sessions' → one run_graph call,
    NOT five julescreate_session calls.

    NEVER fabricate required field values.
    """
    if not _LLM_USABLE:
        yield {"error": "run_graph is disabled because no LLM API key is configured."}
        return
    if not _DB_AVAILABLE:
        yield {"error": "run_graph is disabled because DATABASE_URL is not set."}
        return

    async with _session_lock(session_id):
        async for ev in _stream_graph_impl_inner(
            user_message,
            force_execute,
            session_id,
            caller_scopes=caller_scopes,
            caller_role=caller_role,
            caller_kind=caller_kind,
            caller_source=caller_source,
            bearer_token=bearer_token,
        ):
            yield ev


async def _stream_graph_impl_inner(
    user_message: str,
    force_execute: bool = False,
    session_id: str | None = None,
    caller_scopes=_UNSET,
    caller_role: str | None = None,
    caller_kind=None,
    caller_source: str | None = None,
    bearer_token: str | None = None,
):
    """Inner stream implementation without lock wrapper."""
    if not _LLM_USABLE:
        yield {"error": "run_graph is disabled because no LLM API key is configured."}
        return
    if not _DB_AVAILABLE:
        yield {"error": "run_graph is disabled because DATABASE_URL is not set."}
        return

    # Mode router: oneshot CLI meta-stack vs native root graph.
    from core_graph.runtime.mode_router import RunRequest, stream_oneshot_if_selected
    from core_graph.runtime.mode_router import build_initial_state as _build_initial_state

    req = RunRequest(
        user_message=user_message,
        session_id=session_id,
        force_execute=force_execute,
        caller_scopes=_resolve_caller_scopes(caller_scopes),
        caller_role=caller_role,
        caller_kind=caller_kind,
        caller_source=caller_source,
        bearer_token=bearer_token,
    )
    oneshot_stream = await stream_oneshot_if_selected(req)
    if oneshot_stream is not None:
        async for ev in oneshot_stream:
            yield ev
        return

    import json

    graph = await _get_graph()

    initial_state = _build_initial_state(req)

    def _json_safe(state: dict) -> dict:
        """Strip/serialize messages and non-JSON values for safe transmission."""
        safe = {}
        for k, v in state.items():
            if k == "messages":
                # Only keep text content of messages to avoid serialisation issues
                safe[k] = [{"type": type(m).__name__, "content": getattr(m, "content", str(m))} for m in v]
            else:
                try:
                    json.dumps(v)
                    safe[k] = v
                except (TypeError, ValueError):
                    safe[k] = str(v)
        return safe

    config = _thread_config(session_id)
    thread_id = config["configurable"]["thread_id"]
    _graph_run_t0 = time.perf_counter()
    _graph_run_final_state: dict = {}
    _graph_run_ok = True
    _graph_run_error_type: str | None = None
    try:
        # v3 projections for snapshots + tokens; protocol updates for live node id.
        stream = await graph.astream_events(initial_state, version="v3", config=config)
        q = asyncio.Queue()
        active_node_holder: dict[str, str | None] = {"node": None}

        async def _consume_values():
            try:
                async for snap in stream.values:
                    safe = _json_safe(snap)
                    if active_node_holder["node"]:
                        safe["active_node"] = active_node_holder["node"]
                    await q.put({"type": "values", "state": safe})
            except Exception as e:
                logger.error(f"Error in stream.values: {e}")
                await q.put({"type": "error", "message": str(e)})

        async def _consume_protocol_updates():
            """Emit lightweight active_node pings from the same astream_events run."""
            try:
                async for evt in stream:
                    if not isinstance(evt, dict) or evt.get("method") != "updates":
                        continue
                    for chunk in _update_chunks_from_protocol_event(evt):
                        for node_name in _node_names_from_update_chunk(chunk):
                            active_node_holder["node"] = node_name
                            await q.put(
                                {"type": "values", "state": {"active_node": node_name}}
                            )
            except Exception as e:
                logger.error(f"Error in stream protocol updates: {e}")
                await q.put({"type": "error", "message": str(e)})

        async def _consume_messages():
            try:
                async for msg in stream.messages:
                    node = (getattr(msg, "metadata", {}) or {}).get("langgraph_node")
                    # token-delta iterator if available, otherwise just text
                    if hasattr(msg, "text"):
                        text_attr = msg.text
                        if hasattr(text_attr, "__aiter__"):
                            async for tok in text_attr:
                                await q.put({"type": "token", "node": node, "text": tok})
                        elif isinstance(text_attr, str):
                            await q.put({"type": "token", "node": node, "text": text_attr})
                        elif hasattr(text_attr, "__iter__"):
                            for tok in text_attr:
                                await q.put({"type": "token", "node": node, "text": tok})
                    elif hasattr(msg, "content") and isinstance(msg.content, str):
                        # Some messages might not be chunk iterators, fallback to content
                        await q.put({"type": "token", "node": node, "text": msg.content})
            except Exception as e:
                logger.error(f"Error in stream.messages: {e}")
                await q.put({"type": "error", "message": str(e)})

        async def _gatherer():
            try:
                await asyncio.gather(
                    _consume_values(), _consume_messages(), _consume_protocol_updates()
                )
                await q.put({"type": "end"})
            except Exception as e:
                await q.put({"type": "error", "message": str(e)})

        # Spawn the gatherer; always cancel+await it so an early consumer exit
        # (client disconnect → GeneratorExit) can never orphan it.
        task = asyncio.create_task(_gatherer())
        try:
            while True:
                item = await q.get()
                if item["type"] == "end":
                    try:
                        snap = await graph.aget_state(config)
                        if snap is not None and getattr(snap, "values", None):
                            safe_final = _json_safe(snap.values)
                            _graph_run_final_state = safe_final
                            yield {"type": "values", "state": safe_final}
                    except Exception as exc:
                        logger.debug("authoritative final state fetch skipped: %s", exc)
                    yield item
                    break
                elif item["type"] == "error":
                    _graph_run_ok = False
                    _graph_run_error_type = "graph_stream_error"
                    yield item
                    break
                if item["type"] == "values" and isinstance(item.get("state"), dict):
                    _graph_run_final_state = item["state"]
                yield item
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except Exception as exc:
        logger.exception("Dynamic graph streaming failed")
        _graph_run_ok = False
        _graph_run_error_type = type(exc).__name__
        yield {"type": "error", "message": str(exc)}
    finally:
        # Drop throwaway one-shot checkpoint state once the stream ends/disconnects.
        await _evict_ephemeral_thread(thread_id)
        # feature="graph" telemetry — one row per root-graph run. This is the
        # real invocation path for MCP-driven run_graph calls (a Context is
        # present), so it is instrumented here rather than relying on
        # mode_router._run_root, which the current stack-selection wiring
        # never actually reaches for a "root"-classified request.
        from core_graph.runtime.mode_router import record_graph_run_completion

        record_graph_run_completion(
            run_id=session_id,
            result=_graph_run_final_state,
            latency_ms=int((time.perf_counter() - _graph_run_t0) * 1000),
            ok=_graph_run_ok,
            error_type=_graph_run_error_type,
            mode="root",
        )

async def discover_candidates_impl(user_message: str, top_k: int | None = None) -> dict[str, Any]:
    """Embedder-only dry run: rank candidate tools for a prompt, NO execution.

    Implemented in ``core_graph.discover``; kept here for the public MCP surface
    and for tests that monkeypatch ``mcp_tool._DB_AVAILABLE`` / ``_tools_to_csv``.
    """
    from core_graph.discover import discover_candidates_impl as _impl

    return await _impl(
        user_message,
        top_k=top_k,
        db_available=_DB_AVAILABLE,
        tools_to_csv=_tools_to_csv,
    )


async def run_graph_impl(
    user_message: str,
    force_execute: bool = False,
    ctx: Context | None = None,
    session_id: str | None = None,
    mode: str = "execute",
    caller_scopes=_UNSET,
    caller_role: str | None = None,
    caller_kind=None,
    caller_source: str | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    """Run a natural-language request through the dynamic super-agent graph."""
    if mode == "discover":
        return await discover_candidates_impl(user_message)

    if not _LLM_USABLE:
        return {
            "status": "unavailable",
            "message": (
                "run_graph is disabled because no LLM API key is configured. "
                "Set OPENAI_API_KEY (or ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic) "
                "in your .env file, or add a model to the LLM pool, and restart."
            ),
        }
    if not _DB_AVAILABLE:
        return {
            "status": "unavailable",
            "message": (
                "run_graph is disabled because DATABASE_URL is not set. "
                "The dynamic graph requires Postgres + pgvector. "
                "Set DATABASE_URL and MASTER_KEY and restart."
            ),
        }

    # MCP path: stream live GOAP state/token snapshots to the client as progress
    # notifications so the playground's GoapInline DAG animates step-by-step.
    # Headless path (no Context, e.g. stdio / tests): plain ainvoke, unchanged.
    if ctx is not None:
        final_state = await _run_graph_with_progress(
            user_message,
            force_execute,
            ctx,
            session_id,
            caller_scopes=caller_scopes,
            caller_role=caller_role,
            caller_kind=caller_kind,
            caller_source=caller_source,
            bearer_token=bearer_token,
        )
        if isinstance(final_state, dict) and final_state.get("status") == "error":
            return final_state
        return await _finalize_graph_response(final_state)

    # Headless: mode router selects oneshot vs root; root keeps patchable _get_graph.
    from core_graph.runtime.mode_router import (
        RunRequest,
        build_initial_state,
        select_kind,
    )
    from core_graph.runtime.mode_router import run as run_routed

    req = RunRequest(
        user_message=user_message,
        session_id=session_id,
        force_execute=force_execute,
        caller_scopes=_resolve_caller_scopes(caller_scopes),
        caller_role=caller_role,
        caller_kind=caller_kind,
        caller_source=caller_source,
        bearer_token=bearer_token,
    )
    try:
        if await select_kind(req) == "oneshot":
            routed = await run_routed(req)
            return routed.payload

        async with _session_lock(session_id):
            graph = await _get_graph()
            initial_state = build_initial_state(req)
            config = _thread_config(session_id)
            thread_id = config["configurable"]["thread_id"]
            _t0 = time.perf_counter()
            _ok = True
            _error_type: str | None = None
            _result: dict = {}
            try:
                raw = await graph.ainvoke(initial_state, config=config)
                _result = raw if isinstance(raw, dict) else {"response": raw}
                result = raw
            except Exception as exc:
                _ok = False
                _error_type = type(exc).__name__
                raise
            finally:
                from core_graph.runtime.mode_router import record_graph_run_completion

                record_graph_run_completion(
                    run_id=session_id,
                    result=_result,
                    latency_ms=int((time.perf_counter() - _t0) * 1000),
                    ok=_ok,
                    error_type=_error_type,
                    mode="root",
                )
                await _evict_ephemeral_thread(thread_id)
            return await _finalize_graph_response(
                result if isinstance(result, dict) else {"response": result}
            )
    except Exception as exc:
        logger.exception("Dynamic graph execution failed")
        return {"status": "error", "message": str(exc)}



# Envelope sanitize / finalize — implemented in runtime.envelopes; re-exported
# here so tests and callers can keep patching ``core_graph.mcp_tool.*``.
from core_graph.runtime.envelopes import (  # noqa: F401
    _ENVELOPE_BUDGET,
    _ENVELOPE_COMPRESSION_SHAPE,
    _ENVELOPE_SACRED,
    _TRUNCATED_REF,
    _compress_carrier,
    _finalize_graph_response,
    _sanitize_envelope,
    _tools_to_csv,
    is_oneshot_envelope,
)

async def _run_graph_with_progress(
    user_message: str,
    force_execute: bool,
    ctx: Context,
    session_id: str | None = None,
    caller_scopes=_UNSET,
    caller_role: str | None = None,
    caller_kind=None,
    caller_source: str | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    """Drive the graph via the streaming projection, relaying each event to the
    MCP client as a ``report_progress`` notification, and return the final state.

    Each notification's ``message`` carries a JSON-encoded SSE-style envelope
    (``{"type": "values"|"token", ...}``) that the frontend MCP client decodes to
    feed the live GoapInline DAG. Returns the last ``values`` state snapshot, or
    ``{"status": "error", ...}`` if the stream errored.

    A background keepalive task emits progress during silent phases (e.g. local
    model cold-start) so the MCP client's 60s default timeout is reset.
    """
    import json
    from core_graph.node.helpers.mcp_ctx import (
        ELICIT_KEEPALIVE_INTERVAL_S,
        _has_progress_channel,
        _progress_keepalive,
    )

    keepalive_task: asyncio.Task | None = None
    if _has_progress_channel(ctx):
        keepalive_task = asyncio.create_task(
            _progress_keepalive(ctx, ELICIT_KEEPALIVE_INTERVAL_S, "execution"),
        )

    final_state: dict[str, Any] = {}
    step = 0
    try:
        async for ev in stream_graph_impl(
            user_message,
            force_execute,
            session_id,
            caller_scopes=caller_scopes,
            caller_role=caller_role,
            caller_kind=caller_kind,
            caller_source=caller_source,
            bearer_token=bearer_token,
        ):
            etype = ev.get("type")
            if etype == "values":
                final_state = ev.get("state") or final_state
                step += 1
                await ctx.report_progress(step, None, json.dumps(ev))
            elif etype == "token":
                await ctx.report_progress(step, None, json.dumps(ev))
            elif etype == "error":
                return {"status": "error", "message": ev.get("message", "graph stream failed")}
            elif etype == "end":
                break
            elif "error" in ev:  # unavailable sentinel from stream_graph_impl
                return {"status": "error", "message": ev["error"]}
        return final_state
    finally:
        if keepalive_task is not None:
            keepalive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive_task


@mcp.tool(tags={"core_graph"})
async def discover_tools(
    query: str | None = None,
    plugin: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Discover the tools the run_graph gateway can run behind the scenes.

    In gateway (run_graph unified) mode the MCP client sees only ``run_graph``;
    this tool exposes the live catalog of underlying tools so the client knows
    what natural-language requests it can make. Scans the route index on every
    call, so plugin hot-reloads (enable/disable/add) are reflected immediately.

    Args:
        query: Optional natural-language filter — ranks tools by semantic
            relevance to the query (top matches first). Omit for the full catalog.
        plugin: Optional plugin_id filter (only with the unfiltered catalog).
        limit: Max tools to return. Defaults to the server's candidate_top_k (config-driven RAG breadth, same value run_graph's embedder uses); pass an explicit value to widen the catalog browse.

    Returns:
        ``{"status": "ok", "count": N, "tools": "<csv>"}`` — dense CSV of
        tool cards (operation_id, plugin_id, method, path, summary,
        required_params, tags). The duplicate prose ``overview`` field is omitted.
    """
    if not _DB_AVAILABLE:
        return {"status": "unavailable", "message": "discover_tools requires DATABASE_URL (pgvector route embeddings)."}

    from core_graph.node.helpers import normalize_tool_card
    from utils.server_config import CANDIDATE_TOP_K

    effective_limit = limit if limit is not None else CANDIDATE_TOP_K

    try:
        if query and query.strip():
            from db_layer.embeddings.embeddings_routes import search_routes
            candidates = await search_routes(query.strip(), top_k=max(1, int(effective_limit)))
            if plugin:
                candidates = [c for c in candidates if c.get("plugin_id") == plugin]
        else:
            from db_layer.embeddings.embeddings_routes import list_enabled_routes
            candidates = await list_enabled_routes(plugin_id=plugin)
            candidates = candidates[: max(1, int(effective_limit))]
    except Exception as exc:
        logger.exception("discover_tools failed")
        return {"status": "error", "message": str(exc)}

    tools = [
        {
            **normalize_tool_card(c),
            **({"score": round(float(c["score"]), 3)} if "score" in c and c["score"] is not None else {}),
        }
        for c in candidates
    ]
    return {
        "status": "ok",
        "count": len(tools),
        "tools": _tools_to_csv(tools),
    }


@mcp.tool(tags={"core_graph"})
async def run_graph(
    user_message: str,
    force_execute: bool | None = None,
    session_id: str | None = None,
    mode: str = "execute",
    ctx: Context = CurrentContext(),
) -> dict[str, Any]:
    """Run a natural-language request through the dynamic super-agent graph.

    You are an internal tool executor.

    PARALLELISM RULE: When a request contains multiple INDEPENDENT, DISTINCT goals
    (goals that don't depend on each other's output), emit ALL tool calls in a single response.
    Do not wait for one to complete before calling the next.

    IMPORTANT SCOPE OF PARALLELISM RULE: A single operation repeated over a list
    (create N of X, do the same thing for each item in a set) is NOT multiple independent goals
    — it is ONE run_graph call, never N individual tool calls. Let the server fan out.

    Independent = no output from task A is needed as input to task B, AND the tasks are genuinely distinct goals.
    Sequential = task B needs task A's result first.

    NEVER fabricate required field values.

    OFFLOAD DIRECTIVE: For any multi-step, batch, or repeated request, call run_graph ONCE with
    the whole goal as user_message. run_graph does GOAP planning, fan-out, and replanning
    server-side. Do NOT loop the underlying plugin tools yourself.

    The graph:
      1. Embeds the request and pulls top-k candidate routes from pgvector.
      2. The planner decomposes the request into an ordered plan of 1-N tool
         calls (super-agent mode). Outputs from earlier steps can be threaded
         into later steps via arg_bindings (``$steps[0].id`` → record_id).
      3. Confidence gate: high-confidence short chains execute directly;
         long chains (>3 steps) or low-confidence picks pause for confirmation.
      4. Each step routes to a registered fast-path callable when available,
         falling back to a dynamic HTTP request otherwise. Auth is resolved
         per-step via the plugin's registered auth provider.
      5. Multi-step plans return ``{"status": "ok", "steps_executed": N,
         "data": [...]}``. Long chains return ``confirmation_needed`` first.

    Use this for any multi-step, batch, repeated, or multi-tool request. Examples:
      "Search for project Alpha and post a status update."
      "Fetch the first 3 Notion pages from the search results."
      "Create 5 Jules sessions named A, B, C, D, E." → ONE run_graph call (server fans out),
      NOT five julescreate_session calls.

    Args:
        user_message: Natural-language description of what you want to do.
        force_execute: Bypass the confidence/long-chain confirmation gate and
            execute the best-matched plan directly. Defaults to False.
        session_id: Chat session id; threads cross-turn memory.
        mode: ``"execute"`` (default) runs the full plan. ``"discover"`` does an
            embedder-only dry run — returns the ranked candidate tools/params
            for the request without executing, so a client can confirm the
            intended tool before a real call (gateway discover flow).
        ctx: Context injected by FastMCP to report execution progress.

    Returns:
        The graph's final ``response`` field — a success envelope, an
        error, a need_input prompt, or a confirmation_needed payload with
        the rendered plan. In discover mode, the candidate tool list.

    See also: discover_tools (live catalog of what run_graph can invoke) and
    mode="discover" for a dry-run preview before execution.
    """
    authenticated, principal_role, principal_kind, principal_source = _resolve_mcp_principal()
    if force_execute is None:
        # Authenticated principals skip confidence/write confirm UX; scopes authorize.
        from core.scope_management import default_force_execute_authenticated
        force_execute = default_force_execute_authenticated(authenticated)
    else:
        force_execute = _coerce_force_execute_arg(force_execute)

    scopes = _caller_scopes()
    logger.debug(
        "run_graph: force_execute=%s authenticated=%s role=%s kind=%s scopes=%s source=%s",
        force_execute,
        authenticated,
        principal_role,
        getattr(principal_kind, "value", principal_kind),
        len(scopes) if scopes is not None else None,
        principal_source,
    )

    return await run_graph_impl(
        user_message,
        force_execute,
        ctx,
        session_id,
        mode,
        caller_scopes=scopes if authenticated else _UNSET,
        caller_role=principal_role,
        caller_kind=principal_kind,
        caller_source=principal_source,
        bearer_token=_caller_bearer_token(),
    )

