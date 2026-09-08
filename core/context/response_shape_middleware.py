"""core/context/response_shape_middleware — CSV/shape pipeline for direct MCP tool calls.

The GOAP fast path already shapes proxy-tool output through
``core.proxy_tools.proxy_tool_loader._shape_proxy_result`` (base +
per-op ``response_shapes`` + ``endpoint_meta`` → ``utils.api_utils.apply_response_shape``),
so a proxy list op mounted via ``mcp.add_provider`` returns dense CSV when
called by ``run_graph``. A **direct** MCP ``tools/call`` on that same namespaced
proxy tool skips the fast path entirely — FastMCP forwards straight upstream —
so it never picked up ``endpoint_meta._base.default_response_format: "csv"``.
Same gap for the ~30 hand-written ``@mcp.tool`` modules (jules, memory,
portfolio, terminal, artifact_store, several job_search) that never call
``safe_api_call``.

This middleware closes that gap by running the same ``apply_response_shape``
choke point on every direct tool call result, except:
  * ``GATEWAY_ALWAYS_VISIBLE`` tools (``run_graph`` etc.) — ``core_graph/mcp_tool.py``'s
    own envelope sanitizer owns that contract.
  * Generated ``safe_api_call`` tools that already declare a ``_response_shape``
    parameter in their input schema — they shape internally; running this
    middleware on top would strip/CSV an already-shaped (possibly already-CSV)
    result a second time.
  * Anything disabled via ``graph.direct_call_shaping`` (kill switch, mirrors
    ``graph.proxy_response_shaping``) or listed in ``graph.direct_call_shaping_exclude``.

Callers can still override the shape per call with the same ``_response_shape``
convention the generated tools use (e.g. ``{"response_format": "json"}``) —
no new parameter invented. The key is popped from the call arguments before
forwarding (critical for proxy tools: the upstream server would reject an
unknown kwarg) and threaded into ``apply_response_shape`` as the caller-wins
``shape`` override.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from core.proxy_tools.tool_visibility import GATEWAY_ALWAYS_VISIBLE

logger = logging.getLogger("whiskers")

_SHAPE_ARG_KEY = "_response_shape"


def _direct_call_shaping_config() -> tuple[bool, frozenset[str]]:
    """Read the live kill switch + per-tool exclusion list. Fail-open to defaults."""
    try:
        from utils.config_registry import get_config_registry

        graph_cfg = get_config_registry().server.get("graph", {})
        enabled = bool(graph_cfg.get("direct_call_shaping", True))
        exclude = graph_cfg.get("direct_call_shaping_exclude") or []
        return enabled, frozenset(str(t) for t in exclude if t)
    except Exception:
        logger.debug("direct_call_shaping config unavailable; shaping with defaults", exc_info=True)
        return True, frozenset()


async def _tool_declares_response_shape(context: MiddlewareContext, name: str) -> bool:
    """True when the tool's own input schema already has a ``_response_shape`` param.

    Those are the generated ``safe_api_call`` tools that shape internally —
    running this middleware on top of them would double-shape.

    Resolved via ``core.context.tool_meta`` (local index only) — never
    ``app.get_tool()``, which fans out to every mounted proxy provider on a
    real composite FastMCP server.
    """
    try:
        from core.context.tool_meta import resolve_tool_meta

        meta = await resolve_tool_meta(context, name)
        return meta.declares_shape
    except Exception:
        logger.debug("response_shape schema check failed for %r; assuming not declared", name, exc_info=True)
        return False


async def _resolve_shape_operation_id(context: MiddlewareContext, name: str) -> str:
    """Resolve the unqualified op id used as the ``response_shapes``/``endpoint_meta`` key.

    Hand-written plugin tools use their bare tool name (``ListSessions``,
    ``GetSession``) — same as the config already has entries for. Namespaced
    proxy tools are exposed as ``{proxy_name}_{raw_op}``
    (``core.proxy_tools.tool_visibility.proxy_exposed_name``); strip that
    prefix so the same config entries the GOAP fast path uses
    (``core.proxy_tools.proxy_tool_loader._shape_proxy_result`` keys by
    ``raw_op_id``) apply identically here.
    """
    try:
        from core.context.tool_meta import resolve_tool_meta

        meta = await resolve_tool_meta(context, name)
        plugin_id = meta.plugin_id
    except Exception:
        logger.debug("plugin_id resolution failed for %r; using bare tool name", name, exc_info=True)
        return name

    if plugin_id.startswith("proxy_"):
        proxy_name = plugin_id[len("proxy_"):]
        prefix = f"{proxy_name}_"
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _sanitize_unshaped_result(result: ToolResult, name: str) -> ToolResult:
    """Break cycles in a shaping-failure fallback before FastMCP touches it.

    ``ArtifactOffloadMiddleware``/``ResponseLimitingMiddleware`` run after us,
    and FastMCP's own ``_send_response`` finally calls ``model_dump`` on the
    result — which raises ``ValueError: Circular reference detected`` and
    crashes the whole session if ``structured_content`` self-references. A
    shaping failure is exactly the case most likely to hand back a raw,
    unsanitized payload (that is *why* shaping failed in the first place), so
    never forward it verbatim. Try a cheap JSON round-trip first (proves the
    structure is acyclic and JSON-safe); on failure, fall back to the
    cycle-safe ``strip_base64_fields`` walk to break any self-reference.
    """
    payload = result.structured_content
    if payload is None:
        return result
    try:
        json.dumps(payload, default=str)
        return result  # already acyclic and serializable — nothing to repair
    except (TypeError, ValueError, RecursionError):
        logger.debug("ResponseShapeMiddleware: raw fallback for %r is not JSON-safe; sanitizing", name)
    try:
        from utils.response_shape import strip_base64_fields

        cleaned = strip_base64_fields(payload)
        text = json.dumps(cleaned, default=str)
        safe_structured = json.loads(text)
    except Exception:
        logger.warning("ResponseShapeMiddleware: could not sanitize raw fallback for %r; returning error", name, exc_info=True)
        return ToolResult(
            content=[TextContent(type="text", text=f"Error: tool '{name}' returned an unserializable result.")],
            structured_content={"error": "unserializable_result", "tool": name},
            is_error=True,
            meta=result.meta,
        )
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=safe_structured if isinstance(safe_structured, dict) else {"result": safe_structured},
        meta=result.meta,
    )


class ResponseShapeMiddleware(Middleware):
    """Run the shared strip/project/limit/CSV pipeline on direct MCP tool call results.

    Placed immediately before ``ArtifactOffloadMiddleware`` — shaping already
    runs ``offload_minio`` internally (pipeline step 2), so the offload
    middleware downstream is a cheap idempotent no-op on anything this
    middleware touches, and stays the sole offload path for tools this
    middleware skips.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        """Pop any ``_response_shape`` override, forward the call, then shape the result."""
        name = getattr(context.message, "name", "") or ""

        override_shape: dict[str, Any] | None = None
        if name not in GATEWAY_ALWAYS_VISIBLE:
            arguments = getattr(context.message, "arguments", None) or {}
            if _SHAPE_ARG_KEY in arguments:
                override_shape = arguments.get(_SHAPE_ARG_KEY)
                new_arguments = {k: v for k, v in arguments.items() if k != _SHAPE_ARG_KEY}
                new_message = context.message.model_copy(update={"arguments": new_arguments})
                context = context.copy(message=new_message)

        try:
            result = await call_next(context)
        except ValueError as exc:
            # Convert FastMCP's internal circular-reference ValueError into a normal tool error instead of crashing the session.
            if "circular reference" in str(exc).lower():
                logger.error(
                    "ResponseShapeMiddleware: tool %r returned a self-referencing "
                    "result; returning an error instead of crashing the session",
                    name, exc_info=True,
                )
                return ToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Error: tool '{name}' returned a self-referencing result and could not be serialized.",
                    )],
                    structured_content={"error": "circular_reference", "tool": name},
                    is_error=True,
                )
            raise

        if name in GATEWAY_ALWAYS_VISIBLE:
            return result
        if not isinstance(result, ToolResult):
            return result

        enabled, excluded = _direct_call_shaping_config()
        if not enabled or name in excluded:
            return result

        if override_shape is None and await _tool_declares_response_shape(context, name):
            # Tool already shapes internally (generated safe_api_call tools) —
            # avoid double-shaping/double-CSV-ing its own output.
            return result

        payload = result.structured_content
        if payload is None:
            return result

        try:
            from utils.api_utils import apply_response_shape

            op_id = await _resolve_shape_operation_id(context, name)
            shaped = await apply_response_shape(
                payload,
                operation_id=op_id,
                tool_name=op_id,
                request_params=getattr(context.message, "arguments", None) or {},
                shape=override_shape,
            )
        except Exception:
            logger.warning("ResponseShapeMiddleware: shaping failed for %r; returning raw result", name, exc_info=True)
            return _sanitize_unshaped_result(result, name)

        if isinstance(shaped, dict):
            structured = shaped
        else:
            structured = {"result": shaped}

        try:
            text = shaped if isinstance(shaped, str) else json.dumps(structured, default=str)
        except Exception:
            logger.debug("ResponseShapeMiddleware: text serialization failed for %r; returning raw result", name, exc_info=True)
            return result

        return ToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=structured,
            meta=result.meta,
        )

    async def on_list_tools(self, context: MiddlewareContext, call_next: Any):
        """Advertise the ``_response_shape`` override on covered tools that don't already have it."""
        tools = await call_next(context)

        enabled, excluded = _direct_call_shaping_config()
        if not enabled:
            return tools

        out = []
        for tool in tools:
            name = getattr(tool, "name", "") or ""
            if name in GATEWAY_ALWAYS_VISIBLE or name in excluded:
                out.append(tool)
                continue
            try:
                props = (tool.parameters or {}).get("properties") or {}
                if _SHAPE_ARG_KEY in props:
                    out.append(tool)
                    continue
                new_params = dict(tool.parameters or {})
                new_props = dict(props)
                from utils.response_shape_hints import SHAPE_PARAM_DESCRIPTION

                new_props[_SHAPE_ARG_KEY] = {
                    "type": "object",
                    "description": SHAPE_PARAM_DESCRIPTION,
                }
                new_params["properties"] = new_props
                out.append(tool.model_copy(update={"parameters": new_params}))
            except Exception:
                logger.debug("ResponseShapeMiddleware: schema annotate failed for %r", name, exc_info=True)
                out.append(tool)
        return out
