"""
Wait node utilities.
"""
import asyncio
import logging
from typing import Any, Dict, List

from core_graph.node.execute_step import _execute_single
from core_graph.node.helpers import _resolve_arg_bindings

logger = logging.getLogger(__name__)

def _find_record(resp: Any, match: Dict[str, Any]) -> Dict[str, Any] | None:
    """Find the record in a poll response matching all key/value pairs in `match`.
    Handles a bare list, or a dict wrapping the list under 'data' or 'results'.
    Returns the matched dict or None."""
    records = []
    if isinstance(resp, list):
        records = resp
    elif isinstance(resp, dict):
        if isinstance(resp.get("data"), list):
            records = resp.get("data")
        elif isinstance(resp.get("results"), list):
            records = resp.get("results")
        elif not match:
             return resp

    if not records and isinstance(resp, dict):
        records = [resp]

    for record in records:
        if all(record.get(k) == v for k, v in match.items()):
            return record
    return None

def make_wait_node(ctx):
    """Factory for the async wait node."""
    async def wait_node(state: Dict[str, Any]) -> Dict[str, Any]:
        """Poll an async job's status op until the `until` predicate is met.
        Re-executes the builder-prepared poll payload up to max_polls, sleeping
        interval_s between attempts; aborts on fail_on; errors on timeout."""
        plan = state.get("plan") or []
        idx = state.get("current_step_index", 0)
        step = plan[idx] if 0 <= idx < len(plan) else {}

        until = step.get("until") or {}
        until_field = until.get("field")
        until_val = until.get("equals")
        fail_on = step.get("fail_on") or []
        interval = step.get("interval_s", 10)
        max_polls = step.get("max_polls", 30)

        step_results = state.get("step_results") or []
        match = _resolve_arg_bindings(step.get("match") or {}, step_results)

        for attempt in range(max_polls):
            logger.info(f"Polling attempt {attempt + 1}/{max_polls} for {step.get('operation_id')}")
            out = await _execute_single(step, step_results, state, ctx.route_registry, ctx.llm, ctx.context_params, ctx.api_url)
            resp = out.get("response")

            if isinstance(resp, dict) and resp.get("status") in ("error", "auth_required", "need_input"):
                return {"response": resp}

            record = _find_record(resp, match)

            if record is not None:
                status_val = record.get(until_field)
                if status_val == until_val:
                    logger.info(f"Wait condition met: {until_field} == {until_val}")
                    return {"response": record}
                if status_val in fail_on:
                    error_message = f"Async job failed with status '{status_val}' for {step.get('operation_id')}"
                    logger.error(error_message)
                    return {"response": {"status": "error", "message": error_message}}

            if attempt < max_polls - 1 and interval > 0:
                await asyncio.sleep(interval)

        timeout_message = f"Polling timed out after {max_polls} attempts for {step.get('operation_id')}"
        logger.error(timeout_message)
        return {"response": {"status": "error", "message": timeout_message}}

    return wait_node
