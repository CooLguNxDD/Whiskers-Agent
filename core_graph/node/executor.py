"""
Executor node utilities.
"""
import asyncio
import logging
from core_graph.states import DynamicAPIState
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.helpers import resolve_route_candidate
from core_graph.node.execute_step import _execute_step, _execute_single

logger = logging.getLogger("whiskers")


def _is_soft_failure(res) -> bool:
    """True when an _execute_step result envelope reports failure without raising."""
    if not isinstance(res, dict):
        return False
    if res.get("error") and res.get("status") not in ("ok",):
        # bare {"error": "..."} from exception path
        if "status" not in res:
            return True
    status = res.get("status")
    if status in ("error", "partial", "need_input", "auth_required"):
        return True
    # Nested response envelope from some execute paths
    inner = res.get("response")
    if isinstance(inner, dict) and inner.get("status") in ("error", "partial", "need_input"):
        return True
    return False


def _soft_failure_message(res) -> str:
    if not isinstance(res, dict):
        return str(res)[:2000]
    if res.get("error"):
        return str(res.get("error"))[:2000]
    if res.get("message"):
        return str(res.get("message"))[:2000]
    inner = res.get("response")
    if isinstance(inner, dict):
        return str(inner.get("message") or inner.get("error") or inner.get("status") or "error")[:2000]
    return str(res.get("status") or "error")[:2000]


def make_executor_node(ctx: GraphRuntimeContext):
    """
    Creates an executor node.
    """
    async def executor_node(state: DynamicAPIState) -> dict:
        """Execute the selected step — fast-path callable or HTTP request."""
        payload = state.get("payload") or {}

        # Builder set an error/need_input — pass through.
        if state.get("response") and state["response"].get("status") in ("error", "need_input"):
            return {}

        # Fan-out: if the current plan step declares for_each / for_each_values, run it
        # as a bounded batch via _execute_step. The sequential executor handles a single
        # item only, so without this a "fetch 5 pages" plan would fetch just the first.
        plan = state.get("plan") or []
        idx = state.get("current_step_index", 0)
        cur_step = plan[idx] if 0 <= idx < len(plan) else {}
        if cur_step.get("for_each") or cur_step.get("for_each_values"):
            return await _execute_step(
                cur_step, state.get("step_results") or [], state,
                ctx.route_registry, ctx.llm, ctx.context_params, ctx.api_url,
            )

        # ----- Execution delegation -----
        return await _execute_single(
            cur_step, state.get("step_results") or [], state,
            ctx.route_registry, ctx.llm, ctx.context_params, ctx.api_url,
        )
    return executor_node


def make_step_dispatcher_node(ctx: GraphRuntimeContext):
    """
    Creates a step dispatcher node.
    """
    async def step_dispatcher_node(state: DynamicAPIState) -> dict:
        """Save current step's result; advance (or finalize) the plan.

        Parallel groups: after the first member of a multi-index group completes
        via the normal node path, remaining members are fan-out via gather so
        the whole group finishes before partial/error recovery. Soft error
        envelopes (status:error) count as failures, not successes.
        """
        results = list(state.get("step_results") or [])
        results.append(state.get("response") or {})

        cur_idx = state["current_step_index"]
        next_index = cur_idx + 1
        plan = state.get("plan") or []

        groups = [g for g in (state.get("parallel_groups") or []) if isinstance(g, list)]

        async def _run_parallel_indices(indices: list[int], base_results: list) -> tuple[list, list, int | None]:
            """Execute plan steps at indices concurrently; return (results, errors, first_failed)."""
            parallel_results = await asyncio.gather(*[
                _execute_step(plan[i], base_results, state, ctx.route_registry, ctx.llm,
                              ctx.context_params, ctx.api_url)
                for i in indices
            ], return_exceptions=True)
            errors = []
            first_failed = None
            out = list(base_results)
            for i, res in zip(indices, parallel_results):
                while len(out) <= i:
                    out.append({})
                if isinstance(res, Exception):
                    err_s = repr(res)[:2000]
                    out[i] = {"error": err_s, "status": "error"}
                    errors.append({"index": i, "error": err_s})
                    if first_failed is None:
                        first_failed = i
                elif _is_soft_failure(res):
                    err_s = _soft_failure_message(res)
                    # Preserve the full envelope so recovery can inspect it
                    stored = res if isinstance(res, dict) else {"error": err_s, "status": "error"}
                    if isinstance(stored, dict) and "status" not in stored:
                        stored = {**stored, "status": "error"}
                    out[i] = stored
                    errors.append({"index": i, "error": err_s})
                    if first_failed is None:
                        first_failed = i
                else:
                    out[i] = res
            return out, errors, first_failed

        # Case A: we just finished the first member of a multi-index group
        # (group starts at 0 or any index). Fan out the remaining peers so
        # [[0,1]] actually runs step 1 before error routing.
        finished_group = None
        for g in groups:
            if len(g) > 1 and cur_idx in g and g[0] == cur_idx:
                finished_group = g
                break

        if finished_group is not None:
            remaining = [i for i in finished_group if i != cur_idx]
            # Also treat soft failure of the just-finished first member
            first_res = results[cur_idx] if cur_idx < len(results) else {}
            errors = []
            first_failed = None
            if _is_soft_failure(first_res):
                errors.append({"index": cur_idx, "error": _soft_failure_message(first_res)})
                first_failed = cur_idx

            if remaining:
                results, peer_errors, peer_first = await _run_parallel_indices(remaining, results)
                errors.extend(peer_errors)
                if first_failed is None:
                    first_failed = peer_first

            if errors:
                return {
                    "step_results": results,
                    "current_step_index": max(finished_group),
                    "response": {
                        "status": "partial",
                        "completed_steps": results,
                        "failed_at": first_failed,
                        "failed_steps": errors,
                        "error": errors[0]["error"] if errors else "",
                    },
                }
            next_index = max(finished_group) + 1
        else:
            # Case B: next_index is the first member of a multi-index group that
            # has not been entered yet (e.g. [[0],[1,2]] after step 0). Fan out
            # the entire group concurrently.
            current_group = None
            for g in groups:
                if next_index in g and len(g) > 1 and g[0] == next_index:
                    current_group = g
                    break

            if current_group is not None:
                results, errors, first_failed = await _run_parallel_indices(current_group, results)
                if errors:
                    return {
                        "step_results": results,
                        "current_step_index": max(current_group),
                        "response": {
                            "status": "partial",
                            "completed_steps": results,
                            "failed_at": first_failed,
                            "failed_steps": errors,
                            "error": errors[0]["error"] if errors else "",
                        },
                    }
                next_index = max(current_group) + 1

        if next_index >= len(plan):
            plan_id = state.get("workflow_plan_id")
            if plan_id:
                try:
                    from db_layer.workflow_store import mark_executed
                    await mark_executed(plan_id)
                except Exception as exc:
                    logger.warning("Failed to mark workflow plan as executed: %s", exc)

            execution_id = state.get("execution_id")
            if not execution_id:
                try:
                    from db_layer.execution_store import create_execution, finalize_execution
                    session_id = state.get("session_id")
                    user_query = state.get("user_query") or ""
                    iteration = state.get("iterations", 0)
                    execution_id = await create_execution(
                        session_id=session_id,
                        workflow_plan_id=plan_id,
                        user_query=user_query,
                        iteration=iteration,
                    )
                    await finalize_execution(
                        exec_id=execution_id,
                        step_results=results,
                        working_memory=state.get("working_memory") or {},
                        summary=None,
                        content=None,
                        carry={},
                        status="executed",
                    )
                except Exception as exc:
                    logger.warning("Failed to create/finalize workflow execution: %s", exc)

            return {
                "step_results": results,
                "execution_id": execution_id,
                "response": {
                    "status": "ok",
                    "steps_executed": len(results),
                    "data": results if len(results) > 1 else (results[0] if results else {}),
                },
            }

        next_step = plan[next_index]
        next_selected = resolve_route_candidate(next_step["operation_id"], next_step.get("plugin_id"), state.get("candidates", []), ctx.route_registry)

        if next_selected is None:
            # Surface a clear error rather than silently truncating
            return {
                "step_results": results,
                "response": {
                    "status": "error",
                    "message": (
                        f"Plan step {next_index} references operation "
                        f"'{next_step['operation_id']}' which is not in the candidate pool. "
                        "Try rephrasing your request."
                    ),
                },
            }

        return {
            "step_results": results,
            "current_step_index": next_index,
            "selected": next_selected,
            "payload": None,
            "response": None,
            "retry_count": 0,
        }
    return step_dispatcher_node
