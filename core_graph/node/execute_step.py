"""
Execution step utilities.
"""
import asyncio
import inspect
import logging
import os
import time
from typing import Any

import requests

from core_graph.states import DynamicAPIState
from core_graph.node.helpers import (
    _resolve_arg_bindings,
    resolve_args_from_context,
    _normalize_arg_keys,
    _coerce,
    _to_camel,
)
from utils import safe_api_call
from core.plugin_loader.plugin_registry import get_registry
from core.telemetry import collector
from core.llm_config_service import resolve_step_llm_config
from core.context import current_org_id as _current_org_id


logger = logging.getLogger("whiskers")


def _unwrap_discovery_envelope(result: Any) -> Any:
    """Flatten a status-less {"_meta": ..., "data": ...} discovery envelope.

    ``endpoint_meta._base.include_meta: true`` (config/tools_api_config.json)
    makes ``safe_api_call``/proxy tool calls wrap results in a discovery
    envelope by default — useful for direct/external MCP clients (pagination
    hints, applied_shape, etc.), but every in-graph consumer (validator_node,
    the arg-binding resolver, fold_working_memory) expects a flat payload.
    Left un-unwrapped, ``validator_node`` re-wraps a status-less dict into a
    nested ``data.data`` shape and ``_meta`` scalars leak into memory folding.
    Unwrap right where the raw tool/HTTP result enters graph state, so
    external callers outside the graph keep the envelope untouched.

    ``data`` must not be a bare string: CSV-shaped results (``_response_shape``
    with ``response_format: csv``) put one there — unwrapping to a plain
    string would make ``response`` fail every downstream ``isinstance(...,
    dict)`` check and get hard-wrapped as a false ``status: error`` by
    ``_finalize_graph_response``. Keep the envelope intact in that case so it
    still flattens normally through the ``status``-less dict path.
    """
    if (
        isinstance(result, dict)
        and "status" not in result
        and "_meta" in result
        and "data" in result
        and not isinstance(result["data"], str)
    ):
        return result["data"]
    return result


async def _execute_single(
    step: dict,
    step_results: list[dict],
    state: DynamicAPIState,
    route_registry,
    llm,
    context_params: dict,
    api_url: str,
) -> dict:
    """Execute a single step, either a fast-path callable or an HTTP request."""
    payload = state.get("payload") or {}

    # Builder set an error/need_input — pass through.
    if state.get("response") and state["response"].get("status") in ("error", "need_input"):
        return {}

    # ----- Fast-path branch -----
    if payload.get("__fast_path__") and route_registry is not None:
        route = state["selected"]
        # Resolve org from request context; falls back to "default" for
        # env-var auth paths and single-tenant deployments.
        try:
            org_id = _current_org_id.get()
        except Exception:
            org_id = "default"
        plugin_id = route.get("plugin_id")
        fn = route_registry.fast_path_callable(
            route["operation_id"], plugin_id, instance_id=org_id,
        )
        if fn is None:
            return {"response": {
                "status": "error",
                "message": f"Fast-path callable missing for {route['operation_id']}",
            }}
        
        # Consume resolved_args directly from context_check_node
        args = dict(state.get("resolved_args") or {})

        # Explicitly ensure any pre-filled step args (from GOAP fill_literal_args
        # seed_values or linear planner) are present. resolved_args should already
        # carry them, but merge defensively so literals survive resume/force paths.
        # NOTE: merge FIRST, then prune — seed/context often injects defaults like
        # projectId that platform routes need but portfolio tools (get_projects)
        # do not accept. Pruning before merge used to re-introduce those kwargs
        # and fail with TypeError: unexpected keyword argument 'projectId'.
        step_for_args = state.get("plan", [{}])[state.get("current_step_index", 0)] if state.get("plan") else {}
        base_args = dict(step_for_args.get("args") or {})
        base_args.update(args or {})
        args = base_args

        # Inspect signature to prune extra keyword arguments if function doesn't accept **kwargs
        sig_params = {}
        has_kwargs = False
        try:
            sig = inspect.signature(fn)
            sig_params = sig.parameters
            has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig_params.values())
        except Exception as exc:
            logger.debug("fast-path signature inspect failed: %s", exc)

        # Proxy fast-paths use a dynamic **kwargs wrapper (make_proxy_callable)
        # that forwards to an upstream MCP whose schema can contain keys
        # (e.g. "query") not present in the local Python signature. The
        # builder/context_check already validated against the registered
        # schema, so we must never drop keys for proxy_ plugins.
        plugin_for_prune = (route or {}).get("plugin_id") or ""
        skip_prune_for_proxy = plugin_for_prune.startswith("proxy_") or getattr(fn, "_is_proxy_wrapper", False)
        if not has_kwargs and sig_params and not skip_prune_for_proxy:
            args = _normalize_arg_keys(args, sig_params)
            args = {k: v for k, v in args.items() if k in sig_params}

        # Resolve model name if step is available
        model = None
        try:
            if "plan" in state and "current_step_index" in state:
                idx = state["current_step_index"]
                if 0 <= idx < len(state["plan"]):
                    s = state["plan"][idx]
                    sel = await resolve_step_llm_config(s)
                    if sel:
                        model = sel.get("model")
                    if not model:
                        model = s.get("model")
        except Exception as exc:
            logger.debug("step model resolve failed: %s", exc)

        t0 = time.perf_counter()
        try:
            if inspect.iscoroutinefunction(fn):
                result = await fn(**args)
            else:
                result = fn(**args)
            
            # Record successful fast-path call
            latency_ms = int((time.perf_counter() - t0) * 1000)
            try:
                collector.record_tool_call(
                    tool=route["operation_id"],
                    plugin_id=plugin_id or "unknown",
                    model=model,
                    latency_ms=latency_ms,
                    ok=True,
                    feature="mcp",
                    parent_run_id=state.get("session_id"),
                )
            except Exception as exc:
                logger.debug("telemetry record_tool_call failed: %s", exc)
        except TypeError as exc:
            # Record failed fast-path call (TypeError)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            try:
                collector.record_tool_call(
                    tool=route["operation_id"],
                    plugin_id=plugin_id or "unknown",
                    model=model,
                    latency_ms=latency_ms,
                    ok=False,
                    error_type=type(exc).__name__,
                    feature="mcp",
                    parent_run_id=state.get("session_id"),
                )
            except Exception as exc:
                logger.debug("telemetry record_tool_call failed: %s", exc)
            return {"response": {
                "status": "need_input",
                "message": f"Missing or invalid args for {route['operation_id']}: {exc}",
            }}
        except Exception as exc:
            # Record failed fast-path call (other Exception)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            try:
                collector.record_tool_call(
                    tool=route["operation_id"],
                    plugin_id=plugin_id or "unknown",
                    model=model,
                    latency_ms=latency_ms,
                    ok=False,
                    error_type=type(exc).__name__,
                    feature="mcp",
                    parent_run_id=state.get("session_id"),
                )
            except Exception as exc:
                logger.debug("telemetry record_tool_call failed: %s", exc)
            logger.exception("Fast-path execution failed for %s", route["operation_id"])
            return {"response": {"status": "error", "message": str(exc)}}
        return {"response": _unwrap_discovery_envelope(result)}


    # ----- Dynamic HTTP branch -----
    if not payload.get("url"):
        return {"response": {"status": "error", "message": "No payload to execute."}}

    # Resolve auth headers via the central registry (Phase 4 wiring). Core
    # must not import a specific plugin (guardrail) — with no route_registry
    # or plugin_id, headers stay empty rather than falling back to a
    # hardcoded plugin's auth shim.
    plugin_id = payload.get("plugin_id") or (state.get("selected") or {}).get("plugin_id", "")
    headers: dict[str, str] = {}
    if route_registry is not None and plugin_id:
        try:
            headers = await get_registry().get_auth_headers(plugin_id)
        except Exception as exc:
            logger.warning("Auth resolution failed for plugin '%s': %s", plugin_id, exc)
            return {"response": {
                "status": "auth_required",
                "message": f"Auth resolution failed for {plugin_id}: {exc}",
            }}
        from core.plugin_loader.plugin_auth_registry import is_auth_error
        if is_auth_error(headers):
            # get_auth_headers() degrades to this dict instead of raising (circular
            # delegation / no provider registered) — it must never be forwarded as
            # real HTTP headers below.
            logger.warning("Auth resolution failed for plugin '%s': %s", plugin_id, headers)
            return {"response": {
                "status": "auth_required",
                "message": f"Auth resolution failed for {plugin_id}: {headers.get('error')}",
            }}

    method = payload["method"].upper()
    url = payload["url"]
    query_params = dict(payload.get("query_params") or {})
    body = payload.get("body")

    # Thread prior-step context into the request using resolved_args
    threaded = dict(state.get("resolved_args") or {})
    if threaded:
        if method in ("GET", "DELETE", "HEAD"):
            for k, v in threaded.items():
                query_params.setdefault(k, v)
        else:
            if not isinstance(body, dict):
                body = body if isinstance(body, list) else {}
            if isinstance(body, dict):
                for k, v in threaded.items():
                    body.setdefault(k, v)

    query_params = query_params or None
    body = body or None

    if query_params:
        query_params = {k: v for k, v in query_params.items() if v is not None and v != ""}

    result = await safe_api_call(
        lambda: requests.request(
            method, url,
            params=query_params,
            json=body,
            headers=headers,
            timeout=30,
        ),
        lambda r: r.json(),
        context=f"{method} {url}",
        operation_id=payload.get("operation_id", ""),
        raise_tool_error=False,
    )

    return {"response": _unwrap_discovery_envelope(result)}


async def _execute_step(
    step: dict,
    step_results: list[dict],
    state: DynamicAPIState,
    route_registry,
    llm,
    context_params: dict,
    api_url: str,
) -> dict:
    """Execute a step, handling fan_out/batch planning if required."""
    for_each = step.get("for_each")
    for_each_values = step.get("for_each_values")

    if for_each or for_each_values:
        items = []
        if for_each_values:
            items = for_each_values
        elif for_each:
            resolved = _resolve_arg_bindings({"items": for_each}, step_results)
            items = resolved.get("items", [])
            if not isinstance(items, list):
                items = [items] if items is not None else []

        # Cap the fan-out width to the requested count (e.g. "pick 5").
        limit = step.get("for_each_limit")
        if isinstance(limit, int) and limit > 0:
            items = items[:limit]

        from utils.server_config import MAX_FANOUT, FANOUT_CONCURRENCY
        if len(items) > MAX_FANOUT and not state.get("force_execute"):
            return {"response": {
                "status": "confirmation_needed",
                "message": f"This operation will run {len(items)} times. Limit is {MAX_FANOUT}.",
                "fanout_count": len(items)
            }}
        
        sem = asyncio.Semaphore(FANOUT_CONCURRENCY)
        
        async def run_one(item):
            """Asynchronously run a single item in the fan-out loop, applying bounds."""
            async with sem:
                local_state = dict(state)
                local_args = dict(local_state.get("resolved_args") or {})
                unresolved = local_state.get("unresolved_required") or []
                bound = False
                for k, v in (step.get("arg_bindings") or {}).items():
                    if v == "$item":
                        local_args[k] = item
                        bound = True
                    elif isinstance(v, str) and v.startswith("$item."):
                        # Support "$item.<field>" to extract a nested field from each dict item
                        field = v[len("$item."):]
                        if isinstance(item, dict):
                            val = item.get(field)
                            if val is None:
                                # Try id aliases as fallback (e.g. "$item.id" on a Notion page dict)
                                from core_graph.node.helpers import _ID_ALIASES
                                for alias_k, alias_v in item.items():
                                    if alias_k.lower() in _ID_ALIASES and alias_v is not None:
                                        val = alias_v
                                        break
                            if val is not None:
                                local_args[k] = val
                                bound = True
                        else:
                            local_args[k] = item
                            bound = True
                if not bound and unresolved:
                    local_args[unresolved[0]] = item
                elif not bound:
                    # If there's no unresolved required and no explicit $item binding,
                    # we might just try to bind it to a common id parameter or pass as is.
                    # Since we don't know the schema here, we hope the caller set arg_bindings.
                    pass
                local_state["resolved_args"] = local_args
                return await _execute_single(step, step_results, local_state, route_registry, llm, context_params, api_url)
                
        tasks = [run_one(it) for it in items]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        unwrapped = []
        for r in all_results:
            if isinstance(r, Exception):
                unwrapped.append({"status": "error", "message": str(r)})
            else:
                unwrapped.append(r.get("response", r))
        return {"response": {"status": "ok", "results": unwrapped}}
        
    return await _execute_single(step, step_results, state, route_registry, llm, context_params, api_url)
