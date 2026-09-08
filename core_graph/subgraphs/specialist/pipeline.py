"""Generic specialist pipeline: auto tool discovery → agent execution → synthesis."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("whiskers.core_graph.specialist.pipeline")


async def run_specialist_pipeline(
    goal: str,
    *,
    tenant_id: int,
    tool_globs: list[str] | None = None,
    system_prompt: str | None = None,
    plugin_context: dict[str, Any] | None = None,
    session_id: str | None = None,
    max_steps: int = 12,
    max_seconds: float = 90.0,
    max_tools: int = 40,
    caller_scopes: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
    model: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run the generic MCP specialist stack for an arbitrary goal.

    Three phases:
    1. Auto tool discovery from OperationCatalog (filtered by tool_globs + goal).
    2. Agentic execution via ``run_specialist_agent`` / ``agent_loop``.
    3. Result synthesis into a standardized envelope for specialist_entry/summary.

    Portfolio discover/compose/bake lives in ``plugins.portfolio_plugin.pipeline``
    and is reached via domain registration, not this function.
    """
    q = (goal or "").strip()
    if not q:
        return {
            "status": "error",
            "error": "missing_goal",
            "message": "specialist pipeline requires a goal/query",
            "specialist": True,
        }

    from core_graph.subgraphs.specialist.agents.specialist_agent import (
        discover_tools_from_catalog,
        run_specialist_agent,
    )

    discovered = discover_tools_from_catalog(
        q, tool_globs=tool_globs, max_tools=max_tools
    )
    phases: list[dict[str, Any]] = [
        {
            "phase": "discover_tools",
            "status": "ok",
            "tool_count": len(discovered),
            "tools": [
                f"{t.plugin_id}/{t.operation_id}" for t in discovered[:20]
            ],
        }
    ]

    try:
        result = await run_specialist_agent(
            q,
            tenant_id=int(tenant_id),
            tool_globs=tool_globs,
            system_prompt=system_prompt,
            plugin_context=plugin_context,
            max_steps=max_steps,
            max_seconds=max_seconds,
            max_tools=max_tools,
            caller_scopes=caller_scopes,
            output_schema=output_schema,
            model=model,
        )
    except Exception as exc:
        logger.exception("specialist pipeline agent failed")
        phases.append({"phase": "execute", "status": "error"})
        return _finalize_envelope(
            status="error",
            summary=f"Specialist agent failed: {exc}"[:400],
            phases=phases,
            session_id=session_id,
            extra={"error": "agent_failed", "message": str(exc)[:500]},
        )

    phases.append(
        {
            "phase": "execute",
            "status": result.status,
            "steps": result.steps,
            "tool_call_count": len(result.tool_calls or []),
        }
    )

    output = result.output if isinstance(result.output, dict) else None
    summary = _summarize_result(result, goal=q)
    status = result.status if result.status in ("ok", "partial", "error") else "error"

    extra: dict[str, Any] = {
        "output": output if output is not None else result.output,
        "steps": result.steps,
        "tool_calls": (result.tool_calls or [])[:30],
        "errors": list(result.errors or [])[:20],
    }
    # Promote common structured keys for graph consumers (layout/short_id/etc.)
    if output:
        for key in (
            "layout",
            "short_id",
            "portfolio_job_id",
            "carry",
            "content",
            "data",
        ):
            if key in output and output[key] is not None:
                extra[key] = output[key]

    return _finalize_envelope(
        status=status,
        summary=summary,
        phases=phases,
        session_id=session_id,
        extra=extra,
    )


def _summarize_result(result: Any, *, goal: str) -> str:
    """Build a human-readable summary from an AgentRunResult."""
    output = getattr(result, "output", None)
    if isinstance(output, dict):
        for key in ("summary", "message", "content", "answer", "text"):
            val = output.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:2000]
        # compact JSON fallback
        try:
            import json

            blob = json.dumps(output, default=str)
            if len(blob) > 800:
                blob = blob[:800] + "…"
            return f"Specialist completed for: {goal[:120]}. Result: {blob}"
        except Exception:
            logger.debug("pipeline.py: swallowed exception", exc_info=True)

    status = getattr(result, "status", "error")
    steps = getattr(result, "steps", 0)
    errs = getattr(result, "errors", None) or []
    if status == "error":
        err = errs[0] if errs else "unknown error"
        return f"Specialist failed after {steps} step(s): {err}"[:500]
    if status == "partial":
        return (
            f"Specialist partial result after {steps} step(s) for: {goal[:160]}"
        )
    return f"Specialist completed ({steps} step(s)) for: {goal[:160]}"


def _finalize_envelope(
    *,
    status: str,
    summary: str,
    phases: list[dict[str, Any]],
    session_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    env: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "message": summary,
        "phases": phases,
        "specialist": True,
    }
    if session_id:
        env["session_id"] = session_id
    if extra:
        for k, v in extra.items():
            if v is not None and k not in env:
                env[k] = v
    layout = env.get("layout")
    if isinstance(layout, dict):
        carry = env.get("carry") if isinstance(env.get("carry"), dict) else {}
        if "layout" not in carry:
            env["carry"] = {**carry, "layout": layout}
    return env
