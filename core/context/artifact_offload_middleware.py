"""core/context/artifact_offload_middleware — universal MinIO offload backstop.

Offload-only pass applied to every direct MCP tool call. Complements the
``utils.response_shape``/``safe_api_call``/proxy shaping choke points (which
only cover static, dynamic, and proxy tools) by catching the ~30 hand-written
``@mcp.tool`` modules that return dicts directly (jules, memory, portfolio,
semantic, world_semantic, terminal, several job_search, artifact_store,
goap) and direct proxy tool returns that place large bodies in text/resource
content blocks.

Policy lives in ``utils.response_shape`` (``offload_text_body`` /
``offload_structured_payload``). This middleware is orchestration only:
gate → structured dict pass → content-block adapter loop → rebuild ToolResult.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import EmbeddedResource, TextContent, TextResourceContents

from core.proxy_tools.tool_visibility import GATEWAY_ALWAYS_VISIBLE

logger = logging.getLogger("whiskers")


def _offload_enabled() -> bool:
    """True when artifact offload is enabled and MinIO is reachable."""
    try:
        from utils.response_shape import _minio_offload_default

        return _minio_offload_default()
    except Exception as exc:
        logger.debug(
            "ArtifactOffloadMiddleware: availability check failed, skipping: %s",
            exc,
            exc_info=True,
        )
        return False


def _text_block_adapter(block: Any) -> tuple[str, Callable[[str], Any]] | None:
    """Return ``(text, rebuild_fn)`` for text-bearing content blocks; else None.

    Only typed MCP content blocks reach the middleware: ``ToolResult.__init__``
    routes content through ``_convert_to_content``, which yields ContentBlocks
    (never raw dict shapes).
    """
    if isinstance(block, TextContent):
        return block.text, lambda new: block.model_copy(update={"text": new})

    if isinstance(block, EmbeddedResource) and isinstance(
        getattr(block, "resource", None), TextResourceContents
    ):
        res = block.resource

        def _rebuild(new: str, _block=block, _res=res) -> Any:
            new_res = _res.model_copy(update={"text": new})
            return _block.model_copy(update={"resource": new_res})

        return res.text, _rebuild

    return None


async def _rewrite_content_blocks(
    content: list[Any],
    *,
    tool_name: str,
    request_params: dict | None,
    min_bytes: int,
    max_artifacts: int,
) -> tuple[list[Any], dict | None, list[dict[str, Any]]]:
    """Walk content blocks; offload qualifying text via shared offload_text_body."""
    if not isinstance(content, list):
        return content, None, []

    from utils.response_shape import offload_text_body

    new_content: list[Any] = []
    all_refs: list[dict[str, Any]] = []
    first_structured_override: dict | None = None
    modified = False

    for block in content:
        adapter = _text_block_adapter(block)
        if adapter is None:
            new_content.append(block)
            continue

        text, rebuild = adapter
        remaining = max_artifacts - len(all_refs)
        new_text, structured_override, refs = await offload_text_body(
            text,
            tool_name=tool_name,
            request_params=request_params,
            min_bytes=min_bytes,
            max_artifacts=remaining,
            source="artifact_offload_middleware",
        )
        if refs:
            modified = True
            all_refs.extend(refs)
            if (
                isinstance(structured_override, dict)
                and first_structured_override is None
            ):
                first_structured_override = structured_override
            new_content.append(rebuild(new_text))
        else:
            new_content.append(block)

    return (new_content if modified else content), first_structured_override, all_refs


class ArtifactOffloadMiddleware(Middleware):
    """Offload oversized string leaves in tool results to MinIO.

    Structure-safe (never strips/projects/reshapes — only replaces qualifying
    string leaves with an inline ``[offloaded: ... short_id=...]`` marker),
    idempotent (skips leaves already carrying that marker, so it composes
    safely with the safe_api_call/proxy choke points and the GOAP round-level
    offload), and fail-safe (any error leaves the result untouched).

    **Structured-wins contract:** when ``structured_content`` is a dict and
    offload produces refs *and* ``len(content) <= 1``, content is rebuilt as a
    single ``TextContent`` JSON mirror of the slim dict. When content has
    **multiple** blocks, those blocks are preserved and the text-block pass
    runs over them instead of discarding multi-part content.

    Policy is owned by ``utils.response_shape.offload_text_body`` /
    ``offload_structured_payload`` (shared with the shape pipeline so
    ``artifacts.offloaded`` events always fire with real store refs).

    Handles ``structured_content: dict`` and typed content blocks
    (``TextContent``, ``EmbeddedResource`` with ``TextResourceContents``).

    Deliberately excludes ``GATEWAY_ALWAYS_VISIBLE`` (``run_graph``,
    ``discover_tools``, ``authenticate``, ``complete_authentication``):
    ``core_graph/mcp_tool.py``'s own envelope sanitize/offload already owns
    that contract precisely (sacred fields like ``summary``/``carry`` must
    never be pointer-ified), and a generic offload pass has no notion of
    which fields are sacred there.

    Placed *before* ``ResponseLimitingMiddleware`` in the middleware chain
    (i.e. closer to the actual tool call) so an oversized-but-offloadable
    payload gets a short_id marker instead of being destructively truncated.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> Any:
        """Run the tool, then offload oversized string leaves in its result."""
        result = await call_next(context)

        name = getattr(context.message, "name", "") or ""
        if name in GATEWAY_ALWAYS_VISIBLE:
            return result

        if not isinstance(result, ToolResult):
            return result

        if not _offload_enabled():
            return result

        request_params = getattr(context.message, "arguments", None)
        if request_params is not None and not isinstance(request_params, dict):
            request_params = None

        try:
            from utils.server_config import (
                ARTIFACT_OFFLOAD_MAX_PER_ROUND,
                ARTIFACT_OFFLOAD_MIN_FIELD_BYTES,
            )

            min_bytes = ARTIFACT_OFFLOAD_MIN_FIELD_BYTES
            max_artifacts = ARTIFACT_OFFLOAD_MAX_PER_ROUND
        except Exception as exc:
            logger.warning("artifact_offload_middleware: failed to load config constants: %s", exc)
            min_bytes = 2000
            max_artifacts = 8

        # 1) Structured content (dict only — ToolResult rejects non-dict)
        if isinstance(result.structured_content, dict):
            try:
                from utils.response_shape import offload_structured_payload

                slim, refs = await offload_structured_payload(
                    result.structured_content,
                    tool_name=name,
                    request_params=request_params,
                    min_bytes=min_bytes,
                    max_artifacts=max_artifacts,
                    source="artifact_offload_middleware",
                )
                if refs:
                    content_blocks = list(result.content or [])
                    # Structured-wins: single/empty content → JSON mirror.
                    # Multi-block content is preserved; text pass runs below.
                    if len(content_blocks) <= 1:
                        return ToolResult(
                            content=[
                                TextContent(
                                    type="text", text=json.dumps(slim, default=str)
                                )
                            ],
                            structured_content=slim if isinstance(slim, dict) else None,
                            meta=result.meta,
                        )
                    # Multi-block: keep blocks; still update structured; continue
                    # to content-block pass with remaining budget.
                    result = ToolResult(
                        content=content_blocks,
                        structured_content=slim if isinstance(slim, dict) else None,
                        meta=result.meta,
                    )
                    max_artifacts = max(0, max_artifacts - len(refs))
            except Exception as exc:
                logger.warning(
                    "ArtifactOffloadMiddleware: structured offload failed for tool %r, continuing: %s",
                    name,
                    exc,
                    exc_info=True,
                )

        # 2) Content blocks (when structured had no refs, or multi-block preserved)
        if result.content:
            try:
                new_content, structured_override, refs = await _rewrite_content_blocks(
                    list(result.content),
                    tool_name=name,
                    request_params=request_params,
                    min_bytes=min_bytes,
                    max_artifacts=max_artifacts,
                )
                if refs:
                    structured_out = (
                        structured_override
                        if isinstance(structured_override, dict)
                        else (
                            result.structured_content
                            if isinstance(result.structured_content, dict)
                            else None
                        )
                    )
                    return ToolResult(
                        content=new_content,
                        structured_content=structured_out,
                        meta=result.meta,
                    )
            except Exception as exc:
                logger.warning(
                    "ArtifactOffloadMiddleware: content blocks offload failed for tool %r, leaving result intact: %s",
                    name,
                    exc,
                    exc_info=True,
                )

        return result
