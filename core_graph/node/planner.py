"""
Planner node utilities.
"""
import logging
import asyncio
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from core_graph.states import DynamicAPIState
from core_graph.prompts import INSTRUCTION_PROMPT
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.helpers import (
    _format_candidates,
    _format_plugin_skills,
    _parse_json_response,
    compute_gate,
    format_replan_context,
    resolve_args_from_context,
    LONG_CHAIN_THRESHOLD,
)

logger = logging.getLogger("whiskers")


# ---------------------------------------------------------------------------
# Post-plan stamping chain (shared by GOAP success path)
# ---------------------------------------------------------------------------

def _apply_post_plan_stamping(
    plan: list,
    active_pool_entries: list,
    step_model_policy: dict,
    candidates: list,
) -> tuple[list, list]:
    """Stamp models → inject wait steps → derive parallel groups.

    Order is load-bearing: wait injection lengthens the plan, so parallel
    grouping must run after it; model stamps apply to the pre-wait plan then
    wait steps inherit via inject_wait_steps.
    """
    from core_graph.goap.integrate import assign_step_models, derive_parallel_groups
    from core_graph.goap.wait_inject import inject_wait_steps

    assign_step_models(plan, active_pool_entries, step_model_policy)
    plan = inject_wait_steps(plan, candidates)
    if step_model_policy.get("parallel_enabled"):
        parallel_groups = derive_parallel_groups(plan)
    else:
        parallel_groups = []
    return plan, parallel_groups


# ---------------------------------------------------------------------------
# GOAP-success branch
# ---------------------------------------------------------------------------

async def _plan_via_goap(
    state: DynamicAPIState,
    ctx: GraphRuntimeContext,
    *,
    candidates: list,
    active_pool_entries: list,
    step_model_policy: dict,
    pool_str: str,
    harness_ctx,
    harness_block: str,
    replan_context: list,
    min_rung: int = 0,
) -> dict | None:
    """Attempt hybrid GOAP planning. Returns plan_result dict or None on miss/error."""
    from core_graph.prompts.context_block import format_context_block, history_from_messages
    history = history_from_messages(state.get("messages"))
    ctx_block = format_context_block(history, state.get("working_memory"), state.get("last_summary"))

    from core_graph.prompts.goap_goal_prompt import GOAP_GOAL_PROMPT
    rc = format_replan_context(replan_context) if replan_context else ""
    # Goal-loop focus: when the goap_goal node carried forward the unmet
    # sub-goals, steer re-extraction toward only the remaining gap.
    remaining_facts = state.get("remaining_goal_facts") or []
    focus = (
        f"Remaining sub-goals to achieve (focus the plan here): {remaining_facts}\n\n"
        if remaining_facts else ""
    )
    # Decompose-first hints: sub-tasks drove per-intent retrieval, so the
    # candidate list already spans them; steer goal extraction along the split.
    sub_tasks = state.get("sub_tasks") or []
    decomp = ""
    if sub_tasks:
        decomp = "Decomposed sub-tasks (retrieval was run per sub-task):\n- " + "\n- ".join(sub_tasks) + "\n"
        if state.get("decompose_seed_values"):
            decomp += f"Preliminary seed values: {state['decompose_seed_values']}\n"
        decomp += "\n"
    skills_block = _format_plugin_skills(candidates)
    msgs = [
        SystemMessage(content=GOAP_GOAL_PROMPT),
        HumanMessage(content=f"{ctx_block}\n\n{rc}\n\n{focus}{decomp}{harness_block}{skills_block}Available candidates:\n{_format_candidates(candidates)}\n\nActive Pool Models:\n{pool_str}\n\nUser request: {state['user_query']}"),
    ]
    from core_graph.model_roles.ladder import run_role_ladder

    goal_res = await run_role_ladder(
        "planner_goal",
        state=state,
        node="planner_goal",
        attempt=lambda llm: llm.ainvoke(msgs),
        extract=lambda r: _parse_json_response(r.content),
        fallback_llm=ctx.llm,
        min_rung=min_rung,
        token_usage=state.get("token_usage"),
    )
    parsed_goal = goal_res.value if goal_res.status == "ok" else None
    goal_token_usage = goal_res.token_usage
    goal_model_audit = goal_res.audit_entry("planner_goal")

    from core_graph.goap.integrate import (
        parse_goal_extraction_extended,
        goap_plan_from_goal,
        goap_steps_to_state,
        parse_seed_values,
        fill_literal_args,
        apply_followup_search_topic,
        detect_fanout_count,
        inject_literal_list_fanout,
        collapse_homogeneous_fanout,
        sanitize_seed_values,
        repair_collection_consumer_chain,
    )
    goal, seed_facts, intent, llm_confidence, llm_questions, fan_out = parse_goal_extraction_extended(parsed_goal)
    seed_values = parse_seed_values(parsed_goal)
    # Fold in preliminary literals from the decompose pass; the planner's
    # candidate-aware extraction wins on conflicting keys.
    seed_values = {**(state.get("decompose_seed_values") or {}), **(seed_values or {})}
    # "search up!" is not a topic — substitute the prior user turn before
    # GOAP sees have:query, otherwise web_search is either skipped or run
    # against the verb itself.
    seed_values = apply_followup_search_topic(
        seed_values,
        state.get("user_query"),
        history=history,
        last_summary=state.get("last_summary"),
    )
    # Drop weak free-text id seeds (sessionId="Jules") so GOAP chains
    # collection→by-id instead of short-circuiting get alone.
    seed_values = sanitize_seed_values(seed_values, state.get("working_memory"))
    # Target fan-out count parsed once per goal (e.g. "fetch 5 pages"); persists
    # across loop iterations so the goap_goal node can replan for any shortfall.
    fanout_target = state.get("fanout_target")
    if fanout_target is None:
        fanout_target = detect_fanout_count(state.get("user_query"))
    gplan = await asyncio.to_thread(
        goap_plan_from_goal,
        goal, seed_facts, state.get("working_memory"), candidates,
        seed_values=seed_values,
    )

    if gplan is None or not gplan.steps:
        return None

    frag = goap_steps_to_state(
        gplan, candidates, fan_out=fan_out,
        working_memory=state.get("working_memory"), limit=fanout_target,
    )
    frag["fanout_target"] = fanout_target
    frag["plan"] = collapse_homogeneous_fanout(frag["plan"], limit=fanout_target)
    # Repair get-before-list / missing list producer after seed pollution
    # or dual independent goals (list + get with no depends_on).
    frag["plan"] = repair_collection_consumer_chain(frag["plan"], candidates)
    # Soft-apply high-similarity learned recipe order (cross-run memory).
    if harness_ctx is not None:
        from core_graph.harness import soft_apply_recipe_to_plan

        frag["plan"] = soft_apply_recipe_to_plan(
            frag["plan"], harness_ctx, candidates
        )
    inject_literal_list_fanout(frag["plan"], seed_values, candidates, limit=fanout_target)
    fill_literal_args(
        frag["plan"],
        candidates,
        seed_values,
        state.get("working_memory") or {},
        user_query=state.get("user_query"),
        history=history,
        last_summary=state.get("last_summary"),
    )
    # Re-run chain repair after fill so any leftover raw id args are unbound
    # if a producer is present / insertable.
    frag["plan"] = repair_collection_consumer_chain(frag["plan"], candidates)
    if harness_ctx is not None:
        from core_graph.harness import soft_apply_recipe_to_plan

        frag["plan"] = soft_apply_recipe_to_plan(
            frag["plan"], harness_ctx, candidates
        )
    frag["plan"], frag["parallel_groups"] = _apply_post_plan_stamping(
        frag["plan"], active_pool_entries, step_model_policy, candidates,
    )
    # Keep selected aligned with first plan step after possible reorder.
    first_op = (frag["plan"][0] or {}).get("operation_id") if frag["plan"] else None
    if first_op:
        for c in candidates:
            if c.get("operation_id") == first_op:
                frag["selected"] = c
                break
    # Carry the sanitized seed literals forward (plan steps have them in "args",
    # but having them at top level helps resume/goal-loop nodes and any
    # future defensive fill in context_check/builder).
    frag["seed_values"] = dict(seed_values or {})
    frag["working_memory"] = {
        **(state.get("working_memory") or {}),
        **(seed_values or {}),
    }
    first_selected = frag["selected"]
    confidence, decision = compute_gate(first_selected.get("score", 0.0), llm_confidence, step_count=len(frag["plan"]))

    # Validate every planned step's op_id exists in *this turn's* candidates.
    # Exotic proxy names or goal-loop resume (new embedder on a hint) can
    # cause the LLM to emit a "remembered" did:op that isn't present now;
    # this logs so we can see it, and downstream builder/step_dispatcher
    # will surface a clear error instead of a mysterious tool validation.
    candidates_by_op_check = {c.get("operation_id") for c in candidates if c.get("operation_id")}
    bad_ops = [s.get("operation_id") for s in (frag.get("plan") or []) if s.get("operation_id") not in candidates_by_op_check]
    if bad_ops:
        logger.warning("GOAP plan step(s) reference operation_id not in current candidates for this turn: %s. This can happen with proxy names containing hyphens/case (e.g. proxy_Notion-...) or on resume after a narrow re-embed. Fill/merge guards should still deliver seed literals if the op is registered in the RouteRegistry.", bad_ops)
    clarification = None
    plan_len = len(frag.get("plan") or gplan.steps)
    if decision == "confirm":
        clarification = f"I'll run {plan_len} step(s) toward your goal. Confidence: {confidence:.0%}. Proceed?"
    elif decision == "clarify":
        clarification = f"I'm not fully confident I can reach your goal. Could you clarify?"

    return {
        **frag,
        "confidence": confidence,
        "pgvector_score": first_selected.get("score", 0.0),
        "llm_confidence": llm_confidence,
        "gate_decision": decision,
        "clarification_question": clarification,
        "clarify_questions": llm_questions,
        # CRITICAL: goal stamped on every planning branch.
        "goal": state.get("goal") or intent or state["user_query"],
        # Persist the structured GOAP goal exactly once per goal so the
        # goap_goal node can track satisfaction across loop iterations.
        # Never overwrite the authoritative established set mid-loop.
        "goal_facts": state.get("goal_facts") or list(goal or []),
        "messages": [AIMessage(content=intent or "Planned via GOAP.", additional_kwargs={"internal": True})],
        "response": None,
        "token_usage": goal_token_usage,
        "model_audit": [goal_model_audit],
    }


# ---------------------------------------------------------------------------
# Linear-fallback branch
# ---------------------------------------------------------------------------

async def _plan_via_linear_fallback(
    state: DynamicAPIState,
    ctx: GraphRuntimeContext,
    *,
    candidates: list,
    active_pool_entries: list,
    pool_str: str,
    harness_block: str,
    replan_context: list,
) -> dict:
    """LLM linear planner fallback (both no-instructions and parsed-instructions shapes).

    Always stamps ``goal`` so retry_router recovery remains reachable.
    """
    skills_block = _format_plugin_skills(candidates)
    sub_tasks = state.get("sub_tasks") or []
    decomp = (
        "Decomposed sub-tasks (retrieval was run per sub-task):\n- " + "\n- ".join(sub_tasks) + "\n\n"
        if sub_tasks else ""
    )
    from core_graph.prompts.context_block import format_context_block, history_from_messages
    history = history_from_messages(state.get("messages"))
    ctx_block = format_context_block(
        history, state.get("working_memory"), state.get("last_summary")
    )
    prompt = (
        f"{ctx_block}\n\n{decomp}{harness_block}{skills_block}Available candidates:\n"
        f"{_format_candidates(candidates)}\n\n"
        f"User wants: {state['user_query']}"
    )
    # Static, per-deployment content (prompt + env defaults) forms the system
    # prefix so implicit prompt caching can reuse it; the dynamic candidate
    # list + user query follow, with the most-variable user query last.
    env_defaults = "\n".join(
        f"- {k}: {v}" for k, v in ctx.context_params.items() if k.islower()
    )
    rc = format_replan_context(replan_context) if replan_context else ""
    system_content = f"{INSTRUCTION_PROMPT}\n\nEnvironment Defaults:\n{env_defaults}\n\nAvailable Active Pool Models:\n{pool_str}"
    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=f"{rc}\n\n{prompt}" if rc else prompt),
    ]
    try:
        from core_graph.model_roles.ladder import run_role_ladder

        _raw_holder: dict = {}

        async def _attempt(llm):
            r = await llm.ainvoke(messages)
            _raw_holder["raw"] = r
            return r

        plan_res = await run_role_ladder(
            "planner_linear",
            state=state,
            node="planner_linear",
            attempt=_attempt,
            extract=lambda r: _parse_json_response(r.content),
            fallback_llm=ctx.llm,
            token_usage=state.get("token_usage"),
        )
        response = _raw_holder.get("raw")
        if response is None:
            raise RuntimeError("planner_linear ladder exhausted with no usable response")
        token_usage_update = plan_res.token_usage
        linear_model_audit = plan_res.audit_entry("planner_linear")
        parsed = plan_res.value
    except Exception as exc:
        logger.error("Linear planner llm.ainvoke failed: %s", exc, exc_info=True)
        return {
            "instruction_set": [],
            "plan": [],
            "current_step_index": 0,
            "step_results": [],
            "parallel_groups": [],
            "selected": None,
            "confidence": 0.0,
            "gate_decision": "clarify",
            "clarification_question": "I encountered an error connecting to the language model. Please try again.",
            "clarify_questions": None,
            "messages": [AIMessage(content=f"Error: {str(exc)}", additional_kwargs={"internal": True})],
            "response": {"status": "error", "message": f"Connection/format error: {str(exc)}"},
        }

    # Fallback shape A: LLM failed or returned no steps → best-match single step
    if not parsed or not parsed.get("instructions"):
        best = candidates[0]
        confidence, decision = compute_gate(best["score"], 0.0, step_count=1)
        fallback_instructions = [{
            "intent": "best match",
            "operation_id": best["operation_id"],
            "plugin_id": best.get("plugin_id", ""),
            "notes": "",
        }]
        return {
            "instruction_set": fallback_instructions,
            "workflow_name": "fallback-workflow",
            "workflow_outputs": {},
            "plan": [],
            "current_step_index": 0,
            "step_results": [],
            "parallel_groups": [],
            "selected": best,
            "confidence": confidence,
            "pgvector_score": best["score"],
            "llm_confidence": 0.0,
            "gate_decision": decision,
            "clarification_question": (
                f"I'm not confident about the match. Did you mean: "
                f"{best.get('method', '')} {best.get('path') or best.get('path_template', '')} — "
                f"{best['description']}?"
            ) if decision != "execute" else None,
            "clarify_questions": None,
            "messages": [AIMessage(content=response.content, additional_kwargs={"internal": True})],
            "response": None,
            # Mirrors the GOAP-success branch: without this, a turn that falls
            # back to the linear planner never has state["goal"] set, so
            # retry_router can never route a terminal error to goap_goal
            # recovery and the whole run hard-halts on the first
            # non-whitelisted failure.
            "goal": state.get("goal") or state["user_query"],
            "token_usage": token_usage_update,
            "model_audit": [linear_model_audit],
        }

    # Fallback shape B: parsed instructions
    instruction_set = parsed["instructions"]
    from core_graph.goap.integrate import assign_step_models
    assign_step_models(instruction_set, active_pool_entries)
    workflow_name = parsed.get("name", "custom-workflow")
    workflow_outputs = parsed.get("outputs", {})
    llm_confidence = float(parsed.get("confidence", 0.0))

    candidates_by_op = {c["operation_id"]: c for c in candidates}
    first_selected = candidates_by_op.get(instruction_set[0]["operation_id"], candidates[0])
    confidence, decision = compute_gate(
        first_selected["score"], llm_confidence, step_count=len(instruction_set),
    )

    clarification = None
    step_count = len(instruction_set)
    step_label = f"{step_count} step{'s' if step_count > 1 else ''}"
    if decision == "confirm":
        chain_note = " (long chain — confirmation required)" if step_count > LONG_CHAIN_THRESHOLD else ""
        clarification = (
            f"I'll execute {step_label} starting with "
            f"{first_selected.get('method', '')} "
            f"{first_selected.get('path') or first_selected.get('path_template', '')} — "
            f"{first_selected['description']}. Confidence: {confidence:.0%}{chain_note}. Proceed?"
        )
    elif decision == "clarify":
        clarification = (
            f"Best match for step 1 is {first_selected.get('method', '')} "
            f"{first_selected.get('path') or first_selected.get('path_template', '')} "
            f"(confidence: {confidence:.0%}). Could you clarify your request?"
        )

    return {
        "instruction_set": instruction_set,
        "workflow_name": workflow_name,
        "workflow_outputs": workflow_outputs,
        "plan": [],
        "current_step_index": 0,
        "step_results": [],
        "parallel_groups": [],
        "selected": first_selected,
        "confidence": confidence,
        "pgvector_score": first_selected["score"],
        "llm_confidence": llm_confidence,
        "gate_decision": decision,
        "clarification_question": clarification,
        "clarify_questions": None,
        "messages": [AIMessage(content=parsed.get("reasoning", ""), additional_kwargs={"internal": True})],
        "response": None,
        "token_usage": token_usage_update,
        "model_audit": [linear_model_audit],
        # See note above the other linear-fallback branch: keeps
        # terminal-error recovery routing available uniformly
        # regardless of which planning path produced the plan.
        "goal": state.get("goal") or state["user_query"],
    }


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def make_planner_node(ctx: GraphRuntimeContext):
    """
    Creates a planner node.
    """
    async def planner_node(state: DynamicAPIState) -> dict:
        """LLM decomposes the request into an ordered plan of API calls."""
        candidates = state["candidates"]

        active_pool_entries = []
        from core.llm_config_service import list_pool
        try:
            pool_entries = await list_pool(kind="chat")
            active_pool_entries = [e for e in pool_entries if e.get("is_active")]
            pool_str = "\n".join(
                f"- Name: '{e['name']}', Provider: '{e['provider']}', Model: '{e['model']}', Strength: {e['strength']}"
                for e in active_pool_entries
            )
        except Exception as exc:
            logger.warning("planner: failed to list active chat pool: %s", exc)
            pool_str = ""
        if not pool_str:
            pool_str = "No active pool models available. Fall back to core model."

        step_model_policy = {}
        try:
            from db_layer.step_model_settings_store import get_step_model_policy
            step_model_policy = await get_step_model_policy()
        except Exception:
            logger.debug("planner: step-model policy load failed; using empty policy", exc_info=True)


        if not candidates:
            return {
                "instruction_set": [],
                "plan": [],
                "current_step_index": 0,
                "step_results": [],
                "parallel_groups": [],
                "selected": None,
                "confidence": 0.0,
                "gate_decision": "clarify",
                "clarification_question": (
                    "I couldn't find any API routes matching your request. "
                    "Could you rephrase or be more specific?"
                ),
                "clarify_questions": None,
                "messages": [AIMessage(content="No matching routes found.", additional_kwargs={"internal": True})],
                "response": None,
            }

        last_plan_op_ids = state.get("last_plan_op_ids") or []
        replan_context = list(state.get("replan_context") or [])

        # Core harness: static instructions + RAG plan recipes / anti-patterns
        harness_ctx = None
        harness_block = ""
        try:
            from core_graph.harness import retrieve_harness

            harness_ctx = await retrieve_harness(
                state.get("user_query") or "",
                candidates,
            )
            harness_block = harness_ctx.block if harness_ctx else ""
        except Exception as exc:
            logger.warning("planner: harness retrieve failed: %s", exc)

        for attempt in range(2):
            plan_result = None
            # Try GOAP before the existing LLM linear logic
            try:
                plan_result = await _plan_via_goap(
                    state,
                    ctx,
                    candidates=candidates,
                    active_pool_entries=active_pool_entries,
                    step_model_policy=step_model_policy,
                    pool_str=pool_str,
                    harness_ctx=harness_ctx,
                    harness_block=harness_block,
                    replan_context=replan_context,
                    min_rung=attempt,
                )
            except Exception as exc:
                model_info = "unknown"
                if hasattr(ctx.llm, "model_name"):
                    model_info = getattr(ctx.llm, "model_name")
                elif hasattr(ctx.llm, "model"):
                    model_info = getattr(ctx.llm, "model")
                logger.error("GOAP planning failed, falling back to LLM linear planner. Offending model: %s. Error: %s", model_info, exc, exc_info=True)

            if not plan_result:
                plan_result = await _plan_via_linear_fallback(
                    state,
                    ctx,
                    candidates=candidates,
                    active_pool_entries=active_pool_entries,
                    pool_str=pool_str,
                    harness_block=harness_block,
                    replan_context=replan_context,
                )
                # Connection/format error path mirrors the pre-extraction early
                # return: no replan_context stamp, no identical-plan retry.
                resp = plan_result.get("response")
                if isinstance(resp, dict) and resp.get("status") == "error":
                    return plan_result

            # Check for identical plan failure looping
            new_plan_op_ids = []
            if plan_result.get("plan"):
                new_plan_op_ids = [s.get("operation_id") for s in plan_result["plan"] if s.get("operation_id")]
            elif plan_result.get("instruction_set"):
                new_plan_op_ids = [s.get("operation_id") for s in plan_result["instruction_set"] if s.get("operation_id")]
                
            if attempt == 0 and last_plan_op_ids and new_plan_op_ids == last_plan_op_ids:
                logger.warning("Planner generated identical plan to previous turn. Retrying with feedback.")
                feedback = (
                    "This exact sequence of operations was just attempted and resulted in "
                    "failure or no progress. You MUST select a different sequence of "
                    "operations, or use different parameters/endpoints to achieve the goal."
                )
                replan_context.append({
                    "plan": plan_result.get("plan") or plan_result.get("instruction_set") or [],
                    "feedback": feedback,
                })
                # Durable anti-pattern so the next cold run can avoid this sequence.
                try:
                    from core_graph.harness.episode import record_anti_pattern

                    await record_anti_pattern(
                        state,
                        outcome="identical_plan",
                        detail=feedback,
                        plan_ops=list(new_plan_op_ids),
                    )
                except Exception as exc:
                    logger.debug("identical-plan anti-pattern write skipped: %s", exc)
                continue
                
            plan_result["replan_context"] = replan_context
            return plan_result

    return planner_node

def make_context_check_node(ctx: GraphRuntimeContext):
    """
    Creates a context check node.
    """
    async def context_check_node(state: DynamicAPIState) -> dict:
        """Check prior context before building or executing to resolve expected and required args."""
        plan = state.get("plan") or []
        idx = state.get("current_step_index", 0)
        step = plan[idx] if idx < len(plan) else {}
        route = state.get("selected") or {}
        prior_results = state.get("step_results") or state.get("last_results") or []

        # Resolve org from request context (multi-tenant) — defaults to "default".
        try:
            from core.context import current_org_id as _current_org_id
            org_id = _current_org_id.get()
        except Exception:
            logger.debug("context_check: org_id resolve failed; using default", exc_info=True)
            org_id = "default"
        plugin_id = step.get("plugin_id")

        # Resolve fast-path function if applicable
        fn = None
        is_fast_path = step.get("is_fast_path", False)
        if is_fast_path and ctx.route_registry is not None:
            fn = ctx.route_registry.fast_path_callable(
                step["operation_id"], plugin_id, instance_id=org_id,
            )

        # Merge per-org binding config over context_params (multi-tenant override).
        effective_params = dict(ctx.context_params)
        if ctx.route_registry is not None and plugin_id:
            binding = ctx.route_registry.get_binding(plugin_id, org_id)
            if binding is not None and binding.config:
                effective_params.update(binding.config)

        resolved_args, unresolved_required = resolve_args_from_context(
            step=step,
            step_results=prior_results,
            known_params=effective_params,
            is_fast_path=is_fast_path,
            fn=fn,
            route=route,
            working_memory=state.get("working_memory"),
        )

        return {
            "resolved_args": resolved_args,
            "unresolved_required": unresolved_required,
        }
    return context_check_node
