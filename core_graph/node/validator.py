"""
Validator node utilities.
"""
import logging
from core_graph.states import DynamicAPIState
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.helpers import (
    is_recoverable,
    build_replan_reset,
    should_replan_on_empty,
    _resolve_step_value,
    _ID_ALIASES,
    _coerce,
    _get_parameters_schema,
    detect_failed_params,
    _build_failure_record,
)
from utils.server_config import MAX_REPLANS

logger = logging.getLogger("whiskers")

def make_validator_node(ctx: GraphRuntimeContext):
    """
    Creates a validator node.
    """
    async def validator_node(state: DynamicAPIState) -> dict:
        """Validate the response. Retry once on recoverable errors; replan if unintended and under budget."""
        response = state.get("response")
        retry_count = state.get("retry_count", 0)
        replan_count = state.get("replan_count", 0)
        unresolved = state.get("unresolved_required") or []

        # Check for unintended failure (recoverable, but not a user-input gap unless agentic)
        agentic = bool(state.get("goal") or state.get("force_execute"))
        is_unintended = isinstance(response, dict) and (
            (
                (is_recoverable(response) or response.get("is_yaml_error"))
                and response.get("status") not in ("need_input", "auth_required")
            ) or (
                agentic and response.get("status") == "need_input"
            )
        )

        if is_unintended:
            if replan_count < MAX_REPLANS:
                logger.info(
                    "Unintended failure detected on step execution. Triggering replan "
                    "(replan_count=%d, max_replans=%d).",
                    replan_count, MAX_REPLANS
                )
                return build_replan_reset(state, response, "error")

        # Otherwise, fall back to existing retry loop
        if isinstance(response, dict) and (response.get("status") in ("error", "need_input") or is_recoverable(response)):
            is_terminal = (retry_count >= 1) or (response.get("status") == "error" and not is_recoverable(response))
            if is_terminal:
                resolved_args = state.get("resolved_args")
                if unresolved:
                    msg = f"Execution halted: The following required parameters could not be resolved from context: {', '.join(unresolved)}"
                else:
                    status_code = response.get("status_code", "?")
                    err_msg = response.get("message") or response.get("raw_body") or "empty response"
                    msg = f"Execution failed ({status_code}): {err_msg} | args={resolved_args}"
                err_res = {
                    "status": "error",
                    "message": msg,
                    "unresolved_required": unresolved
                }
                out = {"response": err_res}
                if state.get("goal"):
                    step_results = list(state.get("step_results") or [])
                    step_results.append(err_res)
                    out["step_results"] = step_results
                    # Record the failure into replan/failure memory (without
                    # resetting plan/step_results the way build_replan_reset
                    # does) so goap_goal's repeated-failure guard has real
                    # data to compare against on the next loop iteration
                    # instead of always seeing None.
                    record = _build_failure_record(state, err_res, "error")
                    out["last_failure"] = record
                    out["replan_context"] = list(state.get("replan_context") or []) + [record]
                return out
            return {}

        # Wrap raw list/success responses in a structured envelope
        selected = state.get("selected") or {}
        route_label = (
            f"{selected.get('method', '')} "
            f"{selected.get('path') or selected.get('path_template', '')}"
        ).strip() or "unknown"

        if should_replan_on_empty(state, response):
            return build_replan_reset(state, response, "empty")

        if isinstance(response, list):
            return {"response": {"status": "ok", "data": response, "route": route_label}}
        if isinstance(response, dict) and "status" not in response:
            return {"response": {"status": "ok", "data": response, "route": route_label}}
        return {}
    return validator_node

def make_retry_node(ctx: GraphRuntimeContext):
    """
    Creates a retry node.
    """
    async def retry_node(state: DynamicAPIState) -> dict:
        """Retry node: diagnoses the actual execution failure instead of blindly
        resubmitting it. Pre-existing missing params get the aggressive deep-scan
        across prior step results; a param that was PRESENT but rejected by the
        API (malformed shape/value) is detected from the failure message, stripped
        from resolved_args, and routed to step_resolver for a corrected rebuild —
        otherwise the exact same bad payload would just be sent again."""
        plan = state.get("plan") or []
        idx = state.get("current_step_index", 0)
        step = plan[idx] if idx < len(plan) else {}
        route = state.get("selected") or {}
        prior_results = state.get("step_results") or []
        response = state.get("response") or {}

        # Get already resolved args and unresolved required params
        resolved = dict(state.get("resolved_args") or {})
        unresolved = list(state.get("unresolved_required") or [])

        # Aggressive deep-scan: for each unresolved required parameter, search ALL prior step results
        still_unresolved = []
        for p_name in unresolved:
            search_keys = [p_name]
            p_name_lower = p_name.lower()
            if p_name_lower in _ID_ALIASES:
                search_keys.extend(list(_ID_ALIASES))

            found_val = None
            for res in reversed(prior_results):
                for sk in search_keys:
                    val = _resolve_step_value(res, sk)
                    if val is not None:
                        found_val = val
                        break
                if found_val is not None:
                    break

            if found_val is not None:
                resolved[p_name] = _coerce(found_val)
            else:
                still_unresolved.append(p_name)

        # Diagnose params that were already present but rejected by the API. These
        # never show up in `unresolved_required` (they weren't missing), so without
        # this the deep-scan above is a no-op and the retry just resends the
        # identical malformed payload.
        bad_value_hint = ""
        error_message = response.get("message", "") if isinstance(response, dict) else ""
        if error_message:
            route_schema = _get_parameters_schema(route)
            culprits = detect_failed_params(error_message, list(route_schema.keys()))
            for p_name in culprits:
                if p_name in still_unresolved or resolved.get(p_name) is None:
                    continue
                bad_value_hint = (
                    f"Parameter '{p_name}' was previously set to {resolved[p_name]!r}, "
                    f"which the API rejected: {error_message}. Do not repeat that value — "
                    "construct a corrected one matching the required schema."
                )
                resolved.pop(p_name, None)
                still_unresolved.append(p_name)

        new_retry_count = state.get("retry_count", 0) + 1
        retry_highlight = bad_value_hint
        if still_unresolved and not retry_highlight:
            retry_highlight = f"you previously missed the following required parameters: {', '.join(still_unresolved)}; here is the full prior context."

        return {
            "resolved_args": resolved,
            "unresolved_required": still_unresolved,
            "retry_count": new_retry_count,
            "retry_highlight": retry_highlight,
            "response": None, # Clear error/need_input to re-execute
            "payload": None,  # Force builder to rebuild the request payload
        }
    return retry_node
