"""Write-back of successful plan recipes and failure anti-patterns."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("whiskers.harness")

# Avoid writing the same execution twice in one process
_written_success: set[str] = set()


def _op_ids_from_state(state: dict) -> list[str]:
    plan = state.get("plan") or []
    ops = [
        s.get("operation_id")
        for s in plan
        if isinstance(s, dict) and s.get("operation_id")
    ]
    if ops:
        return ops
    # Fall back to successful step_results operation markers if plan cleared
    for r in state.get("step_results") or []:
        if not isinstance(r, dict):
            continue
        op = r.get("operation_id") or (r.get("step") or {}).get("operation_id")
        if op:
            ops.append(op)
    return ops


def _plugin_ids_from_state(state: dict) -> list[str]:
    plan = state.get("plan") or []
    seen: list[str] = []
    for s in plan:
        if not isinstance(s, dict):
            continue
        pid = s.get("plugin_id") or ""
        if pid and pid not in seen:
            seen.append(pid)
    return seen


def _harness_cfg() -> dict:
    try:
        from utils.server_config import HARNESS_CONFIG

        return dict(HARNESS_CONFIG or {})
    except Exception:
        return {}


async def record_success_recipe(state: dict, *, tenant_id: int | None = None) -> dict | None:
    """Persist a successful op chain as a plan_recipe (no arg values / PII)."""
    cfg = _harness_cfg()
    if not cfg.get("enabled", True) or not cfg.get("write_recipes", True):
        return None

    op_ids = _op_ids_from_state(state)
    if len(op_ids) < 1:
        return None

    exec_id = state.get("execution_id")
    if exec_id is not None:
        key = str(exec_id)
        if key in _written_success:
            return None
        _written_success.add(key)
        # Bound process set
        if len(_written_success) > 500:
            _written_success.clear()

    if tenant_id is None:
        try:
            from core.context import current_tenant_id

            tenant_id = current_tenant_id.get()
        except Exception:
            tenant_id = 1

    query = (state.get("user_query") or state.get("goal") or "")[:400]
    chain = " → ".join(op_ids)
    summary = (state.get("summary") or "")[:300]
    content = (
        f"Goal: {query}\n"
        f"Plan: {chain}\n"
        f"Notes: {summary}".strip()
    )

    try:
        from core.memory import save_plan_recipe

        result = await save_plan_recipe(
            content,
            tenant_id=int(tenant_id),
            op_ids=op_ids,
            plugin_ids=_plugin_ids_from_state(state),
            goal_summary=query,
        )
        logger.info(
            "harness: recorded plan recipe ops=%s status=%s",
            op_ids,
            result.get("status"),
        )
        return result
    except Exception as exc:
        logger.warning("harness: failed to record success recipe: %s", exc)
        return None


async def record_anti_pattern(
    state: dict,
    *,
    outcome: str = "error",
    detail: str = "",
    plan_ops: list[str] | None = None,
    tenant_id: int | None = None,
) -> dict | None:
    """Persist a failed op sequence (structure only)."""
    cfg = _harness_cfg()
    if not cfg.get("enabled", True) or not cfg.get("write_anti_patterns", True):
        return None

    ops = list(plan_ops) if plan_ops is not None else _op_ids_from_state(state)
    if not ops:
        # last_failure plan_ops
        lf = state.get("last_failure") or {}
        if isinstance(lf, dict):
            ops = list(lf.get("plan_ops") or [])
    if not ops:
        return None

    if tenant_id is None:
        try:
            from core.context import current_tenant_id

            tenant_id = current_tenant_id.get()
        except Exception:
            tenant_id = 1

    query = (state.get("user_query") or state.get("goal") or "")[:400]
    detail_s = (detail or "")[:400]
    # Never include resolved_args values
    content = (
        f"Avoid for goal~{query}: plan [{', '.join(ops)}] failed "
        f"({outcome}): {detail_s}"
    )

    try:
        from core.memory import save_anti_pattern

        result = await save_anti_pattern(
            content,
            tenant_id=int(tenant_id),
            plan_ops=ops,
            outcome=outcome,
            detail=detail_s,
        )
        logger.info(
            "harness: recorded anti-pattern ops=%s outcome=%s",
            ops,
            outcome,
        )
        return result
    except Exception as exc:
        logger.warning("harness: failed to record anti-pattern: %s", exc)
        return None


def record_anti_pattern_sync_fire_and_forget(state: dict, **kwargs: Any) -> None:
    """Schedule anti-pattern write without blocking replan reset (best-effort)."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run():
        await record_anti_pattern(state, **kwargs)

    loop.create_task(_run())
