"""The actual tool-calling loop: bind_tools -> dispatch via execute_operation -> observe.

Dispatch mirrors the load-bearing rules already documented in
``plugins/portfolio_plugin/discovery/sources.py::invoke_proxy``: always inject
``_response_shape={"response_format": "json"}``, never prune kwargs for proxy
callables, fall back to ``fast_path_callable`` on ``ExecuteError``, and flatten
the discovery envelope right where it enters the transcript.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core_graph.agent_loop.spec import AgentSpec
from core_graph.agent_loop.toolset import BoundTool, build_toolset, to_langchain_tool_schemas

logger = logging.getLogger("whiskers.core_graph.agent_loop")

_MAX_OBSERVATION_CHARS = 6000
_DISPATCH_CONCURRENCY = 5


@dataclass
class AgentRunResult:
    """Terminal envelope tracking run outputs, step budget exhaustion, and execution trace history."""

    status: str  # ok | partial | error
    output: dict[str, Any] | None
    steps: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)


def _unwrap_envelope(result: Any) -> Any:
    try:
        from core_graph.node.execute_step import _unwrap_discovery_envelope

        return _unwrap_discovery_envelope(result)
    except Exception:
        if (
            isinstance(result, dict)
            and "status" not in result
            and "_meta" in result
            and "data" in result
        ):
            return result["data"]
        return result


async def _await_maybe(fn: Any, kwargs: dict[str, Any]) -> Any:
    check_fn = fn
    while isinstance(check_fn, functools.partial):
        check_fn = check_fn.func
    if hasattr(check_fn, "__call__") and not inspect.isfunction(check_fn):
        try:
            check_fn = check_fn.__call__
        except Exception:
            logger.debug("runner.py: swallowed exception", exc_info=True)
    if inspect.iscoroutinefunction(check_fn):
        return await fn(**kwargs)
    result = await asyncio.to_thread(fn, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _authorize_fast_path(
    plugin_id: str,
    operation_id: str,
    caller_scopes: list[str] | None,
) -> str | None:
    """Return a deny message, or None when the call is affirmatively authorized.

    Fail-closed: missing op metadata or a scope-evaluation error denies the
    call rather than letting it through — this is the choke point that
    protects LLM-chosen tool calls on the fast-path fallback.
    """
    try:
        from core.route_registry.operation_catalog import get_operation_catalog
        from core.context import route_registry

        catalog = get_operation_catalog()
        op = catalog.get(plugin_id, operation_id)
        if op is None:
            route = route_registry.get(operation_id, plugin_id)
            if route is not None:
                op = route.to_operation()
        if op is None:
            return f"No operation metadata for {plugin_id}/{operation_id}"

        from core.scope_management import is_allowed, required_scopes_for_route

        required = set(op.required_scopes) if op.required_scopes else required_scopes_for_route(op.plugin_id, op.tags)
        if not is_allowed(caller_scopes, required):
            return f"Caller lacks required scopes for {plugin_id}/{operation_id}"
        return None
    except Exception as exc_auth:
        logger.warning("agent_loop fast_path_callable scope check error: %s", exc_auth)
        return f"Scope check failed for {plugin_id}/{operation_id}"


async def _dispatch_tool(
    plugin_id: str,
    operation_id: str,
    args: dict[str, Any],
    caller_scopes: list[str] | None = None,
) -> Any:
    """Invoke one tool call: catalog execute_operation, fast-path fallback. Never raises."""
    shaped = {**args, "_response_shape": {"response_format": "json"}}
    try:
        from core.route_registry.execute import ExecuteError, execute_operation

        try:
            result = await execute_operation(
                plugin_id, operation_id, shaped, caller_scopes=caller_scopes,
            )
            return _unwrap_envelope(result)
        except ExecuteError as exc:
            if exc.status == 403 or exc.code == "forbidden":
                return {"status": "error", "message": f"Forbidden: {exc.message}"}
            if exc.code == "invoke_failed":
                # The callable already ran and raised — a fast-path retry would
                # dispatch the upstream call a second time (side-effect duplication).
                return {"status": "error", "message": exc.message[:500]}
            logger.debug(
                "agent_loop execute_operation failed (%s/%s): %s — fast_path fallback",
                plugin_id, operation_id, exc,
            )
    except Exception as exc:
        logger.debug("agent_loop execute_operation path error: %s", exc)

    try:
        from core.context import route_registry

        fn = route_registry.fast_path_callable(operation_id, plugin_id=plugin_id, instance_id="default")
        if fn is None:
            return {"status": "error", "message": f"No fast-path callable for {plugin_id}/{operation_id}"}

        deny = _authorize_fast_path(plugin_id, operation_id, caller_scopes)
        if deny is not None:
            return {"status": "error", "message": f"Forbidden: {deny}"}

        try:
            result = await _await_maybe(fn, shaped)
        except TypeError:
            try:
                result = await _await_maybe(fn, dict(args))
            except Exception as exc2:
                return {"status": "error", "message": str(exc2)[:500]}
        except Exception as exc:
            return {"status": "error", "message": str(exc)[:500]}
        if isinstance(result, dict) and result.get("status") == "error":
            return result
        return _unwrap_envelope(result)
    except Exception as exc:
        return {"status": "error", "message": str(exc)[:500]}


def _truncate_for_transcript(value: Any) -> str:
    try:
        text = json.dumps(value, default=str)
    except Exception:
        text = str(value)
    if len(text) > _MAX_OBSERVATION_CHARS:
        return text[:_MAX_OBSERVATION_CHARS] + f"... [truncated {len(text) - _MAX_OBSERVATION_CHARS} chars]"
    return text


_ROLE_SELECTOR_PREFIX = "role:"


async def _get_llm(spec: AgentSpec):
    """Resolve ``spec``'s model. See ``AgentSpec.model``/``llm_kind`` docstrings.

    Precedence: explicit ``model`` selector > deprecated ``llm_kind`` (treated
    as a selector when not "core") > "core" (``get_graph_core_llm()``).
    Resolves exactly one rung — no escalation ladder (agent loop is
    multi-step/stateful; retrying would re-run side-effecting tool calls).
    """
    from core.llm_config_service import get_graph_core_llm

    selector = spec.model
    if selector is None and spec.llm_kind and spec.llm_kind != "core":
        selector = spec.llm_kind  # back-compat: llm_kind used to be a no-op for non-"core"

    if not selector:
        return await get_graph_core_llm()

    fallback_llm = await get_graph_core_llm()
    from core_graph.model_roles.ladder import resolve_role_first_rung

    if selector.startswith(_ROLE_SELECTOR_PREFIX):
        role_id = selector[len(_ROLE_SELECTOR_PREFIX):].strip()
        llm, _model_name = await resolve_role_first_rung(role_id, fallback=fallback_llm)
        return llm

    from core_graph.model_roles.resolver import resolve_role_llm

    llm, _model_name = await resolve_role_llm(selector, fallback=fallback_llm)
    return llm


def _supports_tool_calling(llm: Any) -> bool:
    """CLI chat models (claude-cli/agy-cli/grok-cli) don't implement real bind_tools."""
    try:
        from core.llm_provider_management import _CLI_PROVIDERS

        provider = getattr(llm, "_llm_type", "") or type(llm).__name__.lower()
        cli_values = {p.value for p in _CLI_PROVIDERS}
        if any(v.replace("-", "") in provider.replace("-", "").replace("_", "") for v in cli_values):
            return False
    except Exception:
        logger.debug("runner.py: swallowed exception", exc_info=True)
    return hasattr(llm, "bind_tools")


def _find_tool(tools: list[BoundTool], name: str) -> BoundTool | None:
    for t in tools:
        if t.tool_name == name:
            return t
    return None


async def run_agent(
    spec: AgentSpec,
    user_prompt: str,
    *,
    tenant_id: int,
    context: dict[str, Any] | None = None,
) -> AgentRunResult:
    """Run one bounded LLM tool-calling loop. Never raises — degrades to status=error.

    ``tenant_id`` is forwarded only to the JSON-protocol fallback
    (``_run_json_protocol``, for CLI-provider chat models without native
    tool-calling); the native ``bind_tools`` loop below relies solely on
    ambient contextvars for tenant scoping.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    tools = build_toolset(spec.tools, max_tools=spec.max_tools)
    schemas = to_langchain_tool_schemas(tools)

    try:
        llm = await _get_llm(spec)
    except Exception as exc:
        logger.exception("agent_loop: LLM resolve failed for %s", spec.name)
        return AgentRunResult(status="error", output=None, steps=0, errors=[f"llm_unavailable: {exc}"])

    if not _supports_tool_calling(llm):
        return await _run_json_protocol(spec, llm, tools, user_prompt, tenant_id=tenant_id, context=context)

    bound_llm = llm.bind_tools(schemas) if schemas else llm

    sys_prompt = spec.system_prompt
    if spec.output_schema:
        sys_prompt += (
            "\n\nWhen you are done, respond with ONLY a JSON object (no prose, no code "
            "fences) matching this schema:\n" + json.dumps(spec.output_schema)
        )

    messages: list[Any] = [SystemMessage(content=sys_prompt), HumanMessage(content=user_prompt)]
    transcript: list[dict[str, Any]] = []
    all_tool_calls: list[dict[str, Any]] = []
    errors: list[str] = []
    started = time.monotonic()
    sem = asyncio.Semaphore(_DISPATCH_CONCURRENCY)

    for step in range(spec.max_steps):
        if time.monotonic() - started > spec.max_seconds:
            errors.append("budget_exceeded_seconds")
            break
        try:
            ai_msg: AIMessage = await bound_llm.ainvoke(messages)
        except Exception as exc:
            logger.exception("agent_loop %s: llm invoke failed at step %d", spec.name, step)
            errors.append(f"llm_invoke_failed: {exc}")
            break

        messages.append(ai_msg)
        calls = getattr(ai_msg, "tool_calls", None) or []
        transcript.append({"step": step, "role": "assistant", "content": _extract_text(getattr(ai_msg, "content", ""))[:2000], "tool_calls": [c.get("name") for c in calls]})

        if not calls:
            output = _parse_final_output(getattr(ai_msg, "content", ""), spec.output_schema)
            if spec.output_schema and output is None:
                # one repair retry
                messages.append(HumanMessage(content="Your last reply was not valid JSON matching the schema. Reply again with ONLY the JSON object."))
                try:
                    ai_msg = await bound_llm.ainvoke(messages)
                    output = _parse_final_output(getattr(ai_msg, "content", ""), spec.output_schema)
                except Exception as exc:
                    errors.append(f"repair_failed: {exc}")
            status = "ok" if (output is not None or not spec.output_schema) else "partial"
            return AgentRunResult(
                status=status,
                output=output,
                steps=step + 1,
                tool_calls=all_tool_calls,
                errors=errors,
                transcript=transcript,
            )

        async def _run_one(call: dict[str, Any]) -> ToolMessage:
            name = call.get("name") or ""
            args = call.get("args") or {}
            call_id = call.get("id") or name
            bt = _find_tool(tools, name)
            async with sem:
                if bt is None:
                    obs: Any = {"status": "error", "message": f"unknown tool {name}"}
                else:
                    try:
                        obs = await _dispatch_tool(bt.plugin_id, bt.operation_id, dict(args), caller_scopes=spec.caller_scopes)
                    except Exception as exc:
                        logger.warning("agent_loop tool dispatch exception for %s: %s", name, exc, exc_info=True)
                        obs = {"status": "error", "message": str(exc)[:500]}
            all_tool_calls.append({"name": name, "args": args, "result": obs})
            return ToolMessage(content=_truncate_for_transcript(obs), tool_call_id=call_id)

        try:
            tool_msgs = await asyncio.gather(*[_run_one(c) for c in calls], return_exceptions=True)
        except Exception as exc:
            errors.append(f"dispatch_failed: {exc}")
            break
        for tm in tool_msgs:
            if isinstance(tm, Exception):
                errors.append(str(tm))
                continue
            messages.append(tm)
            transcript.append({"step": step, "role": "tool", "tool_call_id": tm.tool_call_id, "content": str(tm.content)[:500]})

    return AgentRunResult(
        status="partial",
        output=None,
        steps=spec.max_steps,
        tool_calls=all_tool_calls,
        errors=errors or ["max_steps_exhausted"],
        transcript=transcript,
    )


def _extract_text(content: Any) -> str:
    """Flatten LangChain message content to plain text.

    Several providers (Gemini, Anthropic) return ``content`` as a list of
    content blocks (``[{"type": "text", "text": "..."}]``) rather than a bare
    string — treating that list as ``str(content)`` yields a Python repr
    (single-quoted, ``None``/``True`` literals) that never parses as JSON.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    return str(content or "")


def _parse_final_output(content: Any, output_schema: dict | None) -> dict[str, Any] | None:
    text = _extract_text(content)
    if not output_schema:
        return {"content": text} if text else None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except Exception:
        # Try to find the first { ... } block.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                return None
        return None


async def _run_json_protocol(
    spec: AgentSpec,
    llm: Any,
    tools: list[BoundTool],
    user_prompt: str,
    *,
    tenant_id: int,
    context: dict[str, Any] | None,
) -> AgentRunResult:
    """Fallback loop for chat models without native tool-calling (CLI providers).

    Asks the model to emit ``{"action": {"tool": ..., "args": {...}}}`` or
    ``{"final": {...matching output_schema...}}`` each turn.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    tool_lines = "\n".join(f"- {t.tool_name}: {t.description}" for t in tools)
    protocol_prompt = (
        spec.system_prompt
        + "\n\nYou do not have native tool calling. Available tools:\n"
        + tool_lines
        + "\n\nEach turn, reply with ONLY one JSON object: either "
        '{"action": {"tool": "<tool_name>", "args": {...}}} to call a tool, or '
        '{"final": {...}} matching this schema when done:\n'
        + json.dumps(spec.output_schema or {"content": "string"})
    )
    messages: list[Any] = [SystemMessage(content=protocol_prompt), HumanMessage(content=user_prompt)]
    transcript: list[dict[str, Any]] = []
    all_tool_calls: list[dict[str, Any]] = []
    errors: list[str] = []
    started = time.monotonic()

    for step in range(spec.max_steps):
        if time.monotonic() - started > spec.max_seconds:
            errors.append("budget_exceeded_seconds")
            break
        try:
            ai_msg = await llm.ainvoke(messages)
        except Exception as exc:
            errors.append(f"llm_invoke_failed: {exc}")
            break
        content = getattr(ai_msg, "content", "")
        parsed = _parse_final_output(content, None) or {}
        transcript.append({"step": step, "role": "assistant", "content": str(content)[:2000]})

        if isinstance(parsed, dict) and "final" in parsed:
            final_val = parsed.get("final")
            output = final_val if isinstance(final_val, dict) else None
            return AgentRunResult(
                status="ok" if output is not None else "partial",
                output=output,
                steps=step + 1,
                tool_calls=all_tool_calls,
                errors=errors,
                transcript=transcript,
            )

        action = parsed.get("action") if isinstance(parsed, dict) else None
        if not isinstance(action, dict) or not action.get("tool"):
            messages.append(HumanMessage(content='Reply with ONLY a JSON object: {"action": {...}} or {"final": {...}}.'))
            continue

        name = str(action.get("tool") or "")
        args = action.get("args") or {}
        bt = _find_tool(tools, name)
        if bt is None:
            obs: Any = {"status": "error", "message": f"unknown tool {name}"}
        else:
            try:
                obs = await _dispatch_tool(bt.plugin_id, bt.operation_id, dict(args), caller_scopes=spec.caller_scopes)
            except Exception as exc:
                logger.warning("agent_loop JSON protocol tool dispatch exception for %s: %s", name, exc, exc_info=True)
                obs = {"status": "error", "message": str(exc)[:500]}
        all_tool_calls.append({"name": name, "args": args, "result": obs})
        messages.append(HumanMessage(content=f"Observation for {name}: {_truncate_for_transcript(obs)}"))

    return AgentRunResult(
        status="partial",
        output=None,
        steps=spec.max_steps,
        tool_calls=all_tool_calls,
        errors=errors or ["max_steps_exhausted"],
        transcript=transcript,
    )
