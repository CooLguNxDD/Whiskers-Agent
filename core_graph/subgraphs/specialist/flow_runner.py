"""Generic FlowSpec executor — runs a declared flow's stages in order.

Reuses existing execution primitives rather than inventing new ones:
- ``kind="agentic"`` stages build an ``AgentSpec`` and call
  ``core_graph.agent_loop.run_agent`` (same loop the generic specialist and
  portfolio's layout/discovery agents already use).
- ``kind="deterministic"`` stages dispatch one op via
  ``core.route_registry.execute.execute_operation``.

Returns the same envelope shape ``core_graph.node.specialist_entry`` already
consumes from ``run_specialist`` / ``run_portfolio_pipeline``, so wiring a
flow in is additive: no change to the envelope contract.
"""

from __future__ import annotations

import logging
from typing import Any

from core_graph.agent_loop import AgentSpec, run_agent
from core_graph.subgraphs.specialist.agents.specialist_agent import parse_tool_globs
from core_graph.subgraphs.specialist.blackboard import Blackboard, get_blackboard_store
from core_graph.subgraphs.specialist.flow_spec import FlowSpec, StageSpec

logger = logging.getLogger("whiskers.core_graph.specialist.flow_runner")


class StageFailure(Exception):
    """Raised internally to short-circuit a flow on a fail_closed stage."""

    def __init__(self, stage_id: str, reason: str) -> None:
        super().__init__(reason)
        self.stage_id = stage_id
        self.reason = reason


def _select_stage_model(spec: FlowSpec, stage: StageSpec, *, goal_class: str | None) -> str | None:
    """§8.5 precedence for one agentic stage: stage > flow > plugin manifest > core role."""
    from core_graph.model_roles.manifest_effort import get_manifest_effort
    from core_graph.model_roles.selection import select_agent_model

    manifest = get_manifest_effort(spec.owner) if spec.owner else None
    return select_agent_model(
        stage_model=stage.model,
        stage_effort=stage.effort,
        flow_effort=spec.effort,
        manifest_effort_overrides=manifest.overrides if manifest else None,
        manifest_effort=manifest.effort if manifest else None,
        goal_class=goal_class,
    )


def _default_system_prompt(spec: FlowSpec, stage: StageSpec) -> str:
    base = stage.system_prompt or f"You are executing stage '{stage.id}' of flow '{spec.name}'."
    if stage.output_schema:
        return base
    return base


async def _run_agentic_stage(
    spec: FlowSpec,
    stage: StageSpec,
    *,
    goal: str,
    tenant_id: int,
    caller_scopes: list[str] | None,
    board: Blackboard,
) -> dict[str, Any]:
    tools = parse_tool_globs(list(stage.tool_globs) or None)
    model_selector = _select_stage_model(spec, stage, goal_class=None)
    agent_spec = AgentSpec(
        name=f"{spec.flow_id}:{stage.id}",
        system_prompt=_default_system_prompt(spec, stage),
        tools=tools,
        max_steps=stage.max_steps,
        max_seconds=stage.max_seconds,
        output_schema=stage.output_schema,
        caller_scopes=caller_scopes,
        model=model_selector,
    )
    context = {k: board.get(k) for k in stage.reads if k in board.slots} or None
    result = await run_agent(agent_spec, goal, tenant_id=tenant_id, context=context)
    output = result.output if isinstance(result.output, dict) else {}
    if result.status == "error":
        raise StageFailure(stage.id, f"agent error: {'; '.join(result.errors) or 'unknown'}")
    return output


async def _run_deterministic_stage(
    stage: StageSpec,
    *,
    caller_scopes: list[str] | None,
    board: Blackboard,
) -> dict[str, Any]:
    from core.route_registry.execute import ExecuteError, execute_operation

    plugin_id, _, operation_id = (stage.op or "").partition("/")
    if not plugin_id or not operation_id:
        raise StageFailure(stage.id, f"malformed op reference {stage.op!r} (expected 'plugin/op')")
    args = {k: board.get(k) for k in stage.reads if k in board.slots}
    try:
        result = await execute_operation(
            plugin_id, operation_id, args, caller_scopes=caller_scopes
        )
    except ExecuteError as exc:
        raise StageFailure(stage.id, f"execute_operation failed: {exc.message}") from exc
    return result if isinstance(result, dict) else {"result": result}


def _check_required_outputs(stage: StageSpec, output: dict[str, Any]) -> list[str]:
    return [k for k in stage.required_outputs if k not in output or output[k] is None]


async def _run_one_stage(
    spec: FlowSpec,
    stage: StageSpec,
    *,
    goal: str,
    tenant_id: int,
    caller_scopes: list[str] | None,
    board: Blackboard,
) -> tuple[dict[str, Any], list[str]]:
    """Run one stage once. Returns (output, missing_required_outputs). Never raises StageFailure for on_fail=continue."""
    try:
        if stage.kind == "agentic":
            output = await _run_agentic_stage(
                spec, stage, goal=goal, tenant_id=tenant_id, caller_scopes=caller_scopes, board=board
            )
        else:
            output = await _run_deterministic_stage(stage, caller_scopes=caller_scopes, board=board)
    except StageFailure:
        if stage.on_fail == "continue":
            return {}, list(stage.required_outputs)
        raise

    missing = _check_required_outputs(stage, output)
    return output, missing


async def run_flow(
    spec: FlowSpec,
    goal: str,
    *,
    tenant_id: int,
    session_id: str | None = None,
    caller_scopes: list[str] | None = None,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute every stage of ``spec`` in order; return a specialist envelope.

    Never raises — degrades to ``{"status": "error", ...}`` on any
    unrecoverable stage failure (mirrors ``run_agent``'s never-raises contract
    so callers, notably GOAP's ``execute_step``, don't need a try/except).
    """
    store = get_blackboard_store()
    board = store.create(goal=goal, session_id=session_id, goal_class=None)
    # "goal" is also seeded as an ordinary slot (not just Blackboard.goal) so a
    # deterministic stage can declare "goal" in `reads` like any other input —
    # e.g. a signal-resolution stage parsing company/role out of free text.
    board.write(goal=goal)
    if inputs:
        board.write(**inputs)

    stages_by_id = {s.id: s for s in spec.stages}
    order = [s.id for s in spec.stages]
    rounds_used: dict[str, int] = {}
    phases: list[dict[str, Any]] = []

    idx = 0
    steps_run = 0
    max_total_steps = len(order) * 8  # safety valve against pathological repeat_until loops
    while idx < len(order):
        if steps_run >= max_total_steps:
            logger.warning("flow %s: exceeded max_total_steps, aborting", spec.flow_id)
            return _envelope(
                status="error",
                spec=spec,
                phases=phases,
                session_id=session_id,
                extra={"error": "flow_step_budget_exceeded"},
            )
        steps_run += 1
        stage_id = order[idx]
        stage = stages_by_id[stage_id]

        try:
            output, missing = await _run_one_stage(
                spec, stage, goal=goal, tenant_id=tenant_id, caller_scopes=caller_scopes, board=board
            )
        except StageFailure as exc:
            logger.info("flow %s stage %s failed_closed: %s", spec.flow_id, stage.id, exc.reason)
            phases.append({"phase": stage.id, "status": "error", "reason": exc.reason})
            return _envelope(
                status="error",
                spec=spec,
                phases=phases,
                session_id=session_id,
                extra={"error": "stage_failed", "stage": stage.id, "message": exc.reason},
            )

        if stage.writes:
            board.write(**{k: output.get(k) for k in stage.writes if k in output})
        board.stage_history.append({"stage": stage.id, "missing_required_outputs": missing})
        phases.append({"phase": stage.id, "status": "error" if missing else "ok"})

        if missing:
            logger.info(
                "flow %s stage %s missing required outputs: %s", spec.flow_id, stage.id, missing
            )
            return _envelope(
                status="error",
                spec=spec,
                phases=phases,
                session_id=session_id,
                extra={"error": "required_outputs_missing", "stage": stage.id, "missing": missing},
            )

        ru = stage.repeat_until
        if ru is not None and output.get(ru.field) != ru.equals:
            used = rounds_used.get(stage.id, 0)
            if used < ru.max_rounds:
                rounds_used[stage.id] = used + 1
                target_idx = order.index(ru.on_retry_stage)
                idx = target_idx
                continue

        idx += 1

    return _envelope(
        status="ok",
        spec=spec,
        phases=phases,
        session_id=session_id,
        extra={"carry": dict(board.slots)},
        board=board,
    )


def _envelope(
    *,
    status: str,
    spec: FlowSpec,
    phases: list[dict[str, Any]],
    session_id: str | None,
    extra: dict[str, Any] | None = None,
    board: Blackboard | None = None,
) -> dict[str, Any]:
    summary = (
        f"Flow '{spec.flow_id}' completed ({len(phases)} stage(s))."
        if status == "ok"
        else f"Flow '{spec.flow_id}' failed after {len(phases)} stage(s)."
    )
    env: dict[str, Any] = {
        "status": status,
        "summary": summary,
        "message": summary,
        "phases": phases,
        "specialist": True,
        "specialist_domain": spec.owner or spec.flow_id,
        "flow_id": spec.flow_id,
    }
    if session_id:
        env["session_id"] = session_id
    if extra:
        for k, v in extra.items():
            if v is not None:
                env[k] = v
    if board is not None:
        layout = board.get("layout")
        if isinstance(layout, dict):
            env["layout"] = layout
            carry = env.get("carry") if isinstance(env.get("carry"), dict) else {}
            env["carry"] = {**carry, "layout": layout}
        for key in (
            "short_id",
            "portfolio_job_id",
            "blocks",
            "patched_block_ids",
            "focus_slug",
            "highlight_slugs",
            "dag",
            "pending_job",
            "warnings",
            "overlay_status",
            "recommendations",
        ):
            v = board.get(key)
            if v is not None:
                env[key] = v
        answer = board.get("answer_markdown")
        if isinstance(answer, str) and answer.strip():
            env["summary"] = answer.strip()
            env["message"] = answer.strip()
    return env
