"""
Step resolution utilities.
"""
import logging
from typing import Any
from langchain_core.messages import SystemMessage, HumanMessage
from core_graph.states import DynamicAPIState
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.helpers import _param_hints, _parse_json_response, resolve_route_candidate, resolve_args_from_context

logger = logging.getLogger("whiskers")


def truncate_results_for_prompt(results: Any, max_chars: int = 2000) -> str:
    """Truncates string representation of results to prevent prompt bloat."""
    s = str(results)
    if len(s) > max_chars:
        return s[:max_chars] + "...[truncated]"
    return s


_SYS = (
    "You resolve missing arguments for a single API step from prior context. "
    "Return ONLY a JSON object mapping each requested missing parameter name to a "
    "concrete value drawn from the prior step results, working memory, or user query. "
    "If a value cannot be determined, omit that key. No prose.\n"
    "CRITICAL GROUNDING RULE: When resolving an id-like or url parameter (such as 'id', 'page_id', 'url'), "
    "you MUST copy an existing id or url verbatim from the prior step results or working memory. "
    "Never invent, guess, or hallucinate an id/url value. If only general text or markdown is available "
    "without a specific matching ID/URL, do not return a value for that parameter.\n"
    "INSTRUCTION RULE: When resolving `prompt`, `instruction`, or `task` for an agent/coding session "
    "(e.g. julescreate_session), never return a bare role label such as 'frontend-b', 'backend-a', or "
    "'docs'. Build a multi-line self-contained task brief from the user query: evaluation criteria, "
    "branch/base/diff scope, role focus, and expected report artifacts. Role names are focus labels only."
)

def make_step_resolver_node(ctx: GraphRuntimeContext):
    """
    Create a step resolver node that resolves missing arguments for a single API step.

    This sub-agent attempts to resolve required params in agentic mode or prompts for input/confirmation.
    """
    async def step_resolver_node(state: DynamicAPIState) -> dict:
        """Per-step sub-agent: fill unresolved required params from prior context (agentic),
        else emit need_input. Write ops force a confirm unless force_execute."""
        unresolved = list(state.get("unresolved_required") or [])
        resolved = dict(state.get("resolved_args") or {})

        plan = state.get("plan") or []
        idx = state.get("current_step_index", 0)
        step = plan[idx] if idx < len(plan) else {}
        agentic = bool(state.get("force_execute") or state.get("goal"))

        token_usage_update = None
        if agentic and unresolved:
            try:
                # Find route candidate information for additional context/hints
                route = resolve_route_candidate(
                    step.get("operation_id"), step.get("plugin_id"),
                    state.get("candidates"), ctx.route_registry,
                ) or (state.get("selected") or {})
                hints = _param_hints(route) if route else []
                from core.llm_config_service import resolve_step_llm
                llm = await resolve_step_llm(step)
                prior_results = state.get('step_results') or state.get('last_results')
                wm = state.get('working_memory') or {}
                # Surface harvested plural id lists prominently so the LLM can pick
                # a specific id for the current step (e.g. page_ids from a prior search).
                _ID_LIST_KEYS = ("page_ids", "notion_ids", "ids")
                available_id_lists = {k: wm[k] for k in _ID_LIST_KEYS if k in wm and isinstance(wm[k], list)}
                id_lists_hint = (
                    f"Available id lists from prior search results (use these for id/page_id params): {available_id_lists}\n"
                    if available_id_lists else ""
                )
                retry_highlight = state.get("retry_highlight") or ""
                retry_hint = f"Retry context (a prior attempt just failed): {retry_highlight}\n" if retry_highlight else ""
                human = (
                    f"Step intent: {step.get('intent', '')}\n"
                    f"Missing parameters: {unresolved}\n"
                    f"Param hints: {hints}\n"
                    f"Prior step results: {truncate_results_for_prompt(prior_results)}\n"
                    f"Working memory: {wm}\n"
                    f"{id_lists_hint}"
                    f"{retry_hint}"
                    f"User query: {state.get('user_query', '')}\n"
                    f"Replan context: {state.get('replan_context')}\n"
                    "Return JSON for the missing parameters only."
                )
                resp = await llm.ainvoke([SystemMessage(content=_SYS), HumanMessage(content=human)])
                
                from utils.telemetry import extract_token_usage
                from core_graph.node.helpers import fold_token_usage
                usage = extract_token_usage(resp)
                model_name = getattr(llm, "model", getattr(llm, "model_name", "unknown"))
                token_usage_update = fold_token_usage(state.get("token_usage"), usage, model_name)
                
                parsed = _parse_json_response(getattr(resp, "content", "")) or {}
                from core_graph.goap.integrate.seed_hygiene import (
                    enrich_instruction_value,
                    is_instruction_param,
                    is_weak_instruction_value,
                )
                uq = state.get("user_query") or ""
                for p in unresolved:
                    if parsed.get(p) is not None:
                        val = parsed[p]
                        # Bare role labels are not usable agent prompts — expand or drop.
                        if is_instruction_param(p) and is_weak_instruction_value(val):
                            enriched = enrich_instruction_value(val, uq)
                            if is_weak_instruction_value(enriched):
                                continue
                            val = enriched
                        resolved[p] = val
            except Exception:
                logger.exception("step_resolver: LLM fill failed")
            unresolved = [p for p in unresolved if resolved.get(p) is None]

        if unresolved:
            # Additional context resolution pass before bailing
            route = state.get("selected") or {}
            is_fast_path = step.get("is_fast_path", False)
            fn = None
            if is_fast_path and ctx.route_registry is not None:
                try:
                    from core.context import current_org_id as _current_org_id
                    org_id = _current_org_id.get()
                except Exception:
                    org_id = "default"
                fn = ctx.route_registry.fast_path_callable(
                    step.get("operation_id"), step.get("plugin_id"), instance_id=org_id,
                )
            
            # Use current resolved args as known_params
            resolved_again, still_missing = resolve_args_from_context(
                step=step,
                step_results=state.get("step_results") or state.get("last_results") or [],
                known_params=resolved,
                is_fast_path=is_fast_path,
                fn=fn,
                route=route,
                working_memory=state.get("working_memory"),
            )
            # Merge anything new we found
            for k, v in resolved_again.items():
                if k in unresolved and v is not None:
                    resolved[k] = v
            unresolved = [p for p in unresolved if resolved.get(p) is None]

        out = {}
        if token_usage_update is not None:
            out["token_usage"] = token_usage_update

        if unresolved:
            from core_graph.clarify import build_clarify_questions
            out.update({
                "resolved_args": resolved,
                "unresolved_required": unresolved,
                "response": {
                    "status": "need_input",
                    "missing_params": unresolved,
                    "message": f"Please provide: {', '.join(unresolved)}",
                    "questions": build_clarify_questions(missing_params=unresolved),
                },
            })
            return out

        # Write confirmation is now owned exclusively by permission_gate (supersedes prior
        # ad-hoc block). Gate handles policy + confirmation uniformly for all paths.
        out.update({"resolved_args": resolved, "unresolved_required": []})
        return out
    return step_resolver_node
