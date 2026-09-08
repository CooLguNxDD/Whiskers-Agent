"""Entry router: oneshot CLI meta-stack vs native root GOAP graph."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

from core_graph.runtime.bootstrap import (
    evict_ephemeral_thread,
    get_compiled_graph,
    session_lock,
    thread_config,
)
from core_graph.runtime.registry import StackSpec, get_stack, list_stacks, register_stack

logger = logging.getLogger("whiskers.core_graph.runtime")

RouterKind = Literal["oneshot", "root"]


@dataclass
class RunRequest:
    """Normalized inputs for a run_graph / stream turn."""

    user_message: str
    session_id: str | None = None
    force_execute: bool = False
    caller_scopes: Any = None
    caller_role: str | None = None
    caller_kind: Any = None
    caller_source: str | None = None
    bearer_token: str | None = None
    mode_hint: str | None = None
    goal_class: str | None = None
    extra_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    """Headless router result: oneshot envelope or root graph state."""

    kind: RouterKind
    payload: dict[str, Any]
    stack_id: str


def graph_mode() -> str:
    """Global GRAPH_MODE: auto | oneshot | root (default auto)."""
    raw = (os.environ.get("GRAPH_MODE") or "auto").strip().lower()
    if raw in ("classic", "specialist"):
        return "root"
    if raw in ("auto", "oneshot", "root"):
        return raw
    return "auto"


def _normalize_hint(hint: str | None) -> str | None:
    if not hint:
        return None
    h = hint.strip().lower()
    if h in ("oneshot", "oneshot_cli", "cli"):
        return "oneshot"
    if h in ("root", "classic", "classic_goap", "native", "graph", "specialist", "portfolio"):
        return "root"
    return h


async def oneshot_can_handle(req: RunRequest) -> bool:
    """Whether this turn should short-circuit to CLI oneshot Mode B."""
    mode = graph_mode()
    hint = _normalize_hint(req.mode_hint)
    if mode == "root" or hint == "root":
        return False

    # Import via goap_agent shim so existing unit tests can patch that path.
    from core_graph.goap_agent.oneshot import (
        RECURSION_GUARD_SOURCE,
        active_core_cli_provider,
        oneshot_enabled,
    )

    if req.caller_source == RECURSION_GUARD_SOURCE:
        return False

    forced = mode == "oneshot" or hint == "oneshot"
    if not forced and not oneshot_enabled():
        return False
    if forced and not oneshot_enabled():
        # Explicit GRAPH_MODE/mode_hint still requires oneshot path when provider exists
        pass

    try:
        provider = await active_core_cli_provider()
    except Exception:
        logger.exception(
            "active_core_cli_provider check failed; falling back to native graph"
        )
        return False
    return bool(provider)


def build_initial_state(req: RunRequest) -> dict[str, Any]:
    """Seed DynamicAPIState fields for a native root graph turn."""
    from langchain_core.messages import HumanMessage

    state = {
        "user_query": req.user_message,
        "candidates": [],
        "plan": [],
        "current_step_index": 0,
        "step_results": [],
        "parallel_groups": [],
        "selected": None,
        "confidence": 0.0,
        "gate_decision": "execute",
        "clarification_question": None,
        "payload": None,
        "response": None,
        "retry_count": 0,
        "force_execute": req.force_execute,
        "messages": [HumanMessage(content=req.user_message)],
        "caller_scopes": req.caller_scopes,
        "caller_role": req.caller_role,
        "caller_kind": req.caller_kind,
        "session_id": req.session_id,
        "execution_id": None,
    }
    if req.extra_state:
        state.update(req.extra_state)
    return state


async def _run_oneshot(req: RunRequest) -> dict[str, Any]:
    from core_graph.goap_agent.oneshot import active_core_cli_provider, run_cli_oneshot

    provider = await active_core_cli_provider()
    if not provider:
        raise RuntimeError("oneshot selected but no CLI core provider active")
    return await run_cli_oneshot(
        req.user_message,
        provider=provider,
        session_id=req.session_id,
        bearer_token=req.bearer_token,
    )


async def _stream_oneshot(req: RunRequest) -> AsyncIterator[dict[str, Any]]:
    from core_graph.goap_agent.oneshot import active_core_cli_provider, stream_cli_oneshot

    provider = await active_core_cli_provider()
    if not provider:
        yield {"type": "error", "message": "oneshot selected but no CLI core provider active"}
        return
    async for ev in stream_cli_oneshot(
        req.user_message,
        provider=provider,
        session_id=req.session_id,
        bearer_token=req.bearer_token,
    ):
        yield ev


def record_graph_run_completion(
    *,
    run_id: str | None,
    result: dict[str, Any],
    latency_ms: int,
    ok: bool,
    error_type: str | None,
    mode: str = "root",
) -> None:
    """Emit one feature="graph" telemetry row for a completed root-graph turn.

    Public so both real invocation paths can call it: the MCP ``ctx``-streamed
    path (``core_graph/mcp_tool.py::_stream_graph_impl_inner``, the dominant
    one — real traffic carries a Context) and the headless inline-ainvoke
    fallback in ``run_graph_impl``. ``_run_root`` below also calls this, but
    with the current stack-selection wiring ``mode_router.run()`` never
    actually reaches the root branch for a request already classified "root"
    (see run_graph_impl) — kept for direct/test callers of ``run()``.

    Best-effort field extraction — the graph state has no single canonical
    "run summary" shape (goal_class/step_count only populate on some paths),
    so absent fields are recorded as None rather than guessed. Never allowed
    to fail the turn.
    """
    try:
        from core.telemetry import collector

        response = result.get("response") if isinstance(result, dict) else None
        step_results = result.get("step_results") if isinstance(result, dict) else None
        failed_steps = result.get("failed_steps") if isinstance(result, dict) else None
        terminal_status = None
        if isinstance(response, dict):
            terminal_status = response.get("status")

        collector.record_graph_run(
            run_id=run_id or "unknown",
            mode=mode,
            flow_id=(result.get("flow_id") if isinstance(result, dict) else None),
            goal_class=(result.get("goal_class") if isinstance(result, dict) else None),
            latency_ms=latency_ms,
            ok=ok,
            step_count=(len(step_results) if isinstance(step_results, list) else None),
            failed_steps=(len(failed_steps) if isinstance(failed_steps, list) else None),
            terminal_status=terminal_status,
            error_type=error_type,
        )
    except Exception:
        logger.debug("telemetry record_graph_run failed", exc_info=True)


async def _run_root(req: RunRequest) -> dict[str, Any]:
    graph = await get_compiled_graph()
    initial_state = build_initial_state(req)
    config = thread_config(req.session_id)
    thread_id = config["configurable"]["thread_id"]
    t0 = time.perf_counter()
    ok = True
    error_type: str | None = None
    result: dict[str, Any] = {}
    try:
        raw = await graph.ainvoke(initial_state, config=config)
        result = raw if isinstance(raw, dict) else {"response": raw}
        return result
    except Exception as exc:
        ok = False
        error_type = type(exc).__name__
        raise
    finally:
        record_graph_run_completion(
            run_id=req.session_id,
            result=result,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            ok=ok,
            error_type=error_type,
        )
        await evict_ephemeral_thread(thread_id)


async def select_stack(req: RunRequest) -> StackSpec:
    """Pick the stack for this request (oneshot vs root)."""
    ensure_default_stacks()
    if await oneshot_can_handle(req):
        stack = get_stack("oneshot_cli")
        if stack is not None:
            return stack
    stack = get_stack("root")
    if stack is None:
        raise RuntimeError("root stack is not registered")
    return stack


async def select_kind(req: RunRequest) -> RouterKind:
    """Return ``oneshot`` or ``root`` for this request."""
    stack = await select_stack(req)
    return "oneshot" if stack.id == "oneshot_cli" else "root"


async def run(req: RunRequest) -> RunResult:
    """Headless execution: oneshot → envelope; root → raw graph state."""
    stack = await select_stack(req)
    if stack.id == "oneshot_cli":
        envelope = await _run_oneshot(req)
        return RunResult(kind="oneshot", payload=envelope, stack_id=stack.id)
    async with session_lock(req.session_id):
        state = await _run_root(req)
    return RunResult(kind="root", payload=state, stack_id=stack.id)


async def stream_oneshot_if_selected(
    req: RunRequest,
) -> AsyncIterator[dict[str, Any]] | None:
    """If oneshot is selected, yield its SSE events; else return None (caller runs root)."""
    if await select_kind(req) != "oneshot":
        return None

    async def _gen() -> AsyncIterator[dict[str, Any]]:
        async for ev in _stream_oneshot(req):
            yield ev

    return _gen()


_DEFAULTS_REGISTERED = False


def ensure_default_stacks() -> None:
    """Register oneshot_cli + root stacks once."""
    global _DEFAULTS_REGISTERED
    if _DEFAULTS_REGISTERED and get_stack("root") and get_stack("oneshot_cli"):
        return

    register_stack(
        StackSpec(
            id="oneshot_cli",
            description="CLI Mode-B oneshot meta-agent (claude/agy/grok subprocess)",
            priority=10,
        )
    )
    register_stack(
        StackSpec(
            id="root",
            description="Native LangGraph GOAP root (triage → classic path)",
            priority=100,
        )
    )
    _DEFAULTS_REGISTERED = True


def reset_default_stacks_for_tests() -> None:
    """Allow tests to re-register after clear_stacks()."""
    global _DEFAULTS_REGISTERED
    from core_graph.runtime.registry import clear_stacks

    clear_stacks()
    _DEFAULTS_REGISTERED = False
    ensure_default_stacks()


# Re-export for diagnostics
__all__ = [
    "RunRequest",
    "RunResult",
    "build_initial_state",
    "ensure_default_stacks",
    "graph_mode",
    "list_stacks",
    "oneshot_can_handle",
    "reset_default_stacks_for_tests",
    "run",
    "select_kind",
    "select_stack",
    "stream_oneshot_if_selected",
]
