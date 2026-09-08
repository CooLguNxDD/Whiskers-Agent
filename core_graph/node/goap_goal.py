"""GOAP goal node — structured goal tracking and the goal-loop done/continue decision.

Owns the overarching goal as a first-class GOAP entity. Each loop iteration it folds
step results into working memory, derives the current world state, and compares it
against the persisted ``goal_facts`` (established by the planner on the first plan).
Deterministic fact-satisfaction (``goal_facts and not remaining``) no longer terminates
the loop by itself — every turn with a goal is instead reflected on by the LLM goal-check
verifier (``GOAL_CHECK_PROMPT``), which can demand more evidence before declaring done
(see its RESEARCH COMPLETENESS rule) even when the shallow goal facts are technically
satisfied. This trades one extra LLM call per turn to avoid research/evaluative goals
terminating after a single shallow fact (e.g. one search). On continue, the next planner
iteration is driven toward only the remaining gap via ``remaining_goal_facts`` and a
focused directive. Two independent stall-breakers still force ``done`` deterministically:
a repeated-terminal-failure guard and a no-progress guard (verifier keeps saying continue
but nothing new is gained); both are distinct from — and don't block — the iteration cap
(``MAX_ITERATIONS``).
"""

import json
import logging
from langchain_core.messages import HumanMessage, SystemMessage
from core_graph.states import DynamicAPIState
from core_graph.prompts.goal_check_prompt import GOAL_CHECK_PROMPT
from core_graph.goap.goal_loop import (
    fold_working_memory,
    parse_goal_check,
    decide_goal_loop,
    evaluate_goal_facts,
    world_from_memory,
    build_goal_directive,
    accumulate_executed_ops,
    collect_fetched_ids,
    format_research_notes,
    DEFAULT_MAX_ITERATIONS,
)
from utils.server_config import MAX_REPLANS
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.helpers import format_replan_context, _parse_json_response

logger = logging.getLogger("whiskers")


def _sanitize_continue_seeds(state: DynamicAPIState) -> dict:
    """Strip weak/failed id seeds before a goal-loop replan continues.

    Prevents re-poisoning GOAP with the same garbage sessionId that just failed.
    """
    from core_graph.goap.integrate.seed_hygiene import (
        sanitize_seed_values,
        strip_failed_seed_keys,
    )

    seeds = dict(state.get("seed_values") or {})
    mem = state.get("working_memory") or {}
    last_fail = state.get("last_failure")
    failed_args = {}
    if isinstance(last_fail, dict):
        failed_args = dict(last_fail.get("args") or {})
    # Also strip args from the current error response if present
    resp = state.get("response") or {}
    if isinstance(resp, dict) and resp.get("status") in ("error", "partial"):
        # failed_steps may list per-index errors; prefer last_failure args
        pass
    seeds, _ = strip_failed_seed_keys(seeds, mem, failed_args)
    return sanitize_seed_values(seeds, mem)


def make_goap_goal_node(ctx: GraphRuntimeContext):
    """
    Creates a GOAP goal node.
    """
    async def goap_goal_node(state: DynamicAPIState) -> dict:
        """Track structured goal facts and decide whether to continue or finish the goal-loop."""
        iterations = state.get("iterations", 0) + 1
        old_mem = state.get("working_memory") or {}
        working_memory = fold_working_memory(old_mem, state.get("step_results") or [])
        executed_op_ids = accumulate_executed_ops(
            state.get("executed_op_ids"), state.get("plan"), state.get("step_results")
        )
        last_summary = state.get("summary") or state.get("last_summary")
        goal = state.get("goal")
        goal_facts = state.get("goal_facts") or []
        max_iter = state.get("max_iterations") or DEFAULT_MAX_ITERATIONS

        # Deterministic GOAP satisfaction: world-state (folded memory) vs goal facts.
        world = world_from_memory(working_memory)
        achieved, remaining = evaluate_goal_facts(goal_facts, world, executed_op_ids)

        # Helper to build/preserve a terminal response.
        def get_terminal_response() -> dict:
            """Construct a fallback terminal response dict from the current state.

            Always re-attach portfolio layout from working_memory when present so
            CatPortfolio can apply response.carry.layout after goap_goal → summary_node
            (round_summary folds layout into working_memory; final summary enriches).
            """
            existing = state.get("response")
            if isinstance(existing, dict):
                out = dict(existing)
            elif last_summary:
                out = {
                    "status": "ok",
                    "message": last_summary,
                    "summary": last_summary,
                    "working_memory": working_memory,
                }
            else:
                out = {
                    "status": "incomplete",
                    "message": f"Goal not completed within {iterations} iteration(s).",
                    "working_memory": working_memory,
                }

            # Ensure design_layout / emit_layout payload survives the goal-loop hop.
            layout = None
            carry = out.get("carry") if isinstance(out.get("carry"), dict) else {}
            if isinstance(carry.get("layout"), dict):
                layout = carry["layout"]
            elif isinstance(out.get("layout"), dict):
                layout = out["layout"]
            elif isinstance(working_memory.get("layout"), dict):
                layout = working_memory["layout"]
            if layout is not None:
                out["layout"] = layout
                out["carry"] = {**carry, "layout": layout}
            return out

        # Base tracking fields echoed on every return so they persist across iterations.
        tracking = {
            "iterations": iterations,
            "working_memory": working_memory,
            "last_summary": last_summary,
            "goal_facts": goal_facts,
            "achieved_facts": achieved,
            "executed_op_ids": executed_op_ids,
        }

        # No-progress baseline: prefer pre_round_memory (stashed by round_summary
        # before it folded step_results). Topology is round_summary → goap_goal, so
        # working_memory is already post-fold when we arrive; comparing against
        # old_mem alone would always look like "no progress" after an idempotent
        # re-fold. Fall back to old_mem when the key is absent (non-loop paths).
        baseline = state.get("pre_round_memory")
        if not isinstance(baseline, dict):
            baseline = old_mem
        gained_nothing = True
        for k, v in working_memory.items():
            if baseline.get(k) != v:
                gained_nothing = False
                break

        current_failure = None
        resp = state.get("response")
        if isinstance(resp, dict) and resp.get("status") in ("error", "need_input"):
            current_failure = resp.get("message") or resp.get("status")

        last_fail = state.get("last_failure")
        last_fail_str = None
        if isinstance(last_fail, dict):
            last_fail_str = last_fail.get("detail") or last_fail.get("message")
        elif isinstance(last_fail, str):
            last_fail_str = last_fail

        is_repeated_failure = bool(current_failure and last_fail_str and current_failure == last_fail_str)
        repeat_failure_count = state.get("repeat_failure_count", 0)
        if is_repeated_failure and gained_nothing:
            repeat_failure_count += 1
        else:
            repeat_failure_count = 0

        # No-progress guard against verifier "continue" spins: if the verifier keeps
        # saying continue but each replan gained no new memory AND no new executed op,
        # stop after 2 stalled rounds. Genuine research turns add new memory/ops each
        # round (new fetches, new candidates), so this never trips on real progress —
        # only on a re-plan that produced nothing new. Bound stays MAX_ITERATIONS.
        prev_op_count = len(state.get("executed_op_ids") or [])
        gained_no_ops = len(executed_op_ids) <= prev_op_count
        no_progress_rounds = state.get("no_progress_rounds", 0)
        if gained_nothing and gained_no_ops and state.get("goal_loop_decision") == "continue":
            no_progress_rounds += 1
        else:
            no_progress_rounds = 0
        tracking["no_progress_rounds"] = no_progress_rounds

        if no_progress_rounds >= 2:
            logger.warning("Goal loop detected no-progress replan spin. Stopping early.")
            return {
                **tracking,
                "remaining_goal_facts": remaining,
                "response": get_terminal_response(),
                "goal_loop_decision": "done",
                "repeat_failure_count": repeat_failure_count,
            }

        if repeat_failure_count >= 2:
            logger.warning("Goal loop detected repeated terminal failure with no progress. Stopping early.")
            return {
                **tracking,
                "remaining_goal_facts": remaining,
                "response": get_terminal_response(),
                "goal_loop_decision": "done",
                "repeat_failure_count": repeat_failure_count,
            }

        # Count-aware multi-fetch gate: when the goal asked for N items (e.g. "fetch 5
        # pages") but the fan-out delivered fewer (partial failure / capped width),
        # replan to fetch only the still-missing ids. Deterministic, no LLM call.
        fanout_target = state.get("fanout_target")
        if isinstance(fanout_target, int) and fanout_target > 1:
            candidate_ids = (
                working_memory.get("page_ids")
                or working_memory.get("notion_ids")
                or working_memory.get("ids")
                or []
            )
            if isinstance(candidate_ids, list) and candidate_ids:
                candidate_ids = [str(c) for c in candidate_ids if c]
                need = min(fanout_target, len(candidate_ids))
                prev_fetched = working_memory.get("fetched_ids") or []
                new_fetched = collect_fetched_ids(state.get("step_results") or [], candidate_ids)
                fetched = list(dict.fromkeys([*map(str, prev_fetched), *new_fetched]))
                working_memory["fetched_ids"] = fetched
                fetch_replans = int(working_memory.get("fetch_replans") or 0)
                if len(fetched) < need and iterations < max_iter and fetch_replans < MAX_REPLANS:
                    fetched_set = set(fetched)
                    remaining_ids = [c for c in candidate_ids if c not in fetched_set][: need - len(fetched)]
                    if remaining_ids:
                        working_memory["pending_fetch_ids"] = remaining_ids
                        working_memory["fetch_replans"] = fetch_replans + 1
                        logger.info(
                            "GOAP goal node: fetched %d/%d target page(s); replanning for %d remaining.",
                            len(fetched), need, len(remaining_ids),
                        )
                        last_plan_op_ids = [s.get("operation_id") for s in state.get("plan") or [] if s.get("operation_id")]
                        directive = (
                            f"Fetch the following {len(remaining_ids)} remaining page(s) by id "
                            f"(do not search again): {remaining_ids}"
                        )
                        return {
                            **tracking,
                            "remaining_goal_facts": remaining,
                            "user_query": directive,
                            "plan": [],
                            "current_step_index": 0,
                            "step_results": [],
                            "last_results": state.get("step_results") or [],
                            "yaml_workflow": "",
                            "instruction_set": [],
                            "selected": None,
                            "response": None,
                            "goal_loop_decision": "continue",
                            # Keep client/user headless intent across replan (e.g. portfolio
                            # force_execute=True). Resetting to False re-opens the confidence
                            # gate and returns "Proceed?" to clients without elicitation UI.
                            "force_execute": bool(state.get("force_execute")),
                            "sub_tasks": None,  # re-decompose the focused directive
                            "seed_values": _sanitize_continue_seeds(state),
                            "execution_id": None,
                            "repeat_failure_count": repeat_failure_count,
                            "last_plan_op_ids": last_plan_op_ids,
                        }
                # Target satisfied (or budget hit) — clear the re-fetch queue.
                working_memory.pop("pending_fetch_ids", None)

        # NOTE: structured-goal-satisfied no longer short-circuits here. Deterministic
        # fact-satisfaction (goal_facts and not remaining) is still surfaced to the LLM
        # verifier below via goal_block ("Remaining sub-goals: []") so it can confirm
        # completion cheaply, but the verifier always gets a chance to demand more
        # evidence (see RESEARCH COMPLETENESS rule in GOAL_CHECK_PROMPT) before the loop
        # ends. This trades one extra LLM call per turn for correctness on open-ended /
        # evaluative goals that were previously declared done after a single shallow fact.

        # Fast exit: no overarching goal, or iteration budget exhausted.
        if not goal or iterations >= max_iter:
            return {
                **tracking,
                "remaining_goal_facts": remaining,
                "response": get_terminal_response(),
                "goal_loop_decision": "done",
                "repeat_failure_count": repeat_failure_count,
            }

        # LLM verification fallback (sub-goals remain or goal_facts absent).
        new_token_usage = state.get("token_usage")
        goal_audit: dict | None = None
        try:
            rc = format_replan_context(state.get("replan_context")) if state.get("replan_context") else ""
            goal_block = f"Goal: {goal}"
            if goal_facts:
                goal_block += (
                    f"\n\nGoal facts: {goal_facts}"
                    f"\nAchieved sub-goals: {achieved}"
                    f"\nRemaining sub-goals: {remaining}"
                )
            prior_findings = format_research_notes(state.get("research_notes"))
            prior_block = (
                f"Prior round findings:\n{prior_findings}\n\n" if prior_findings else ""
            )
            msgs = [
                SystemMessage(content=GOAL_CHECK_PROMPT),
                HumanMessage(
                    content=f"{goal_block}\n\n"
                    f"Working memory: {working_memory}\n\n"
                    f"{rc}\n\n"
                    f"{prior_block}"
                    f"Step results:\n{json.dumps(state.get('step_results') or [], default=str)[:4000]}"
                ),
            ]
            from core_graph.model_roles.ladder import run_role_ladder

            goal_res = await run_role_ladder(
                "goal_verifier",
                state=state,
                node="goal_verifier",
                attempt=lambda llm: llm.ainvoke(msgs),
                extract=lambda r: _parse_json_response(r.content),
                fallback_llm=ctx.llm,
                token_usage=state.get("token_usage"),
            )
            new_token_usage = goal_res.token_usage
            goal_audit = goal_res.audit_entry("goal_verifier")
            parsed = goal_res.value if goal_res.status == "ok" else None
            done, reason, next_hint = parse_goal_check(parsed)
            decision = decide_goal_loop(goal, iterations, max_iter, done)
            if decision == "continue":
                last_plan_op_ids = [s.get("operation_id") for s in state.get("plan") or [] if s.get("operation_id")]
                # Drive the next sub-plan toward the remaining gap. Prefer the structured
                # remaining facts directive; fall back to the LLM next_hint or goal.
                directive = build_goal_directive(remaining, next_hint) or next_hint or goal
                # Retriage to shared triage when same-stack replan is the wrong fix.
                from core_graph.runtime.retriage import build_retriage_reset, should_retriage

                probe = {**state, **tracking, "replan_count": state.get("replan_count") or 0}
                if should_retriage(probe):
                    reset = build_retriage_reset(probe)
                    return {
                        **tracking,
                        **reset,
                        "remaining_goal_facts": remaining,
                        "user_query": state.get("original_query") or directive,
                        "last_results": state.get("step_results") or [],
                        "goal_loop_decision": "retriage",
                        "token_usage": new_token_usage,
                        "model_audit": [goal_audit] if goal_audit else [],
                        "last_plan_op_ids": last_plan_op_ids,
                        "last_failure": current_failure or state.get("last_failure"),
                    }
                return {
                    **tracking,
                    "remaining_goal_facts": remaining,
                    "user_query": directive,                   # focused re-plan target
                    "plan": [],
                    "current_step_index": 0,
                    "step_results": [],
                    "last_results": state.get("step_results") or [],
                    "yaml_workflow": "",
                    "instruction_set": [],
                    "selected": None,
                    "response": None,                          # INVARIANT: clear before re-plan
                    "goal_loop_decision": "continue",
                    # Preserve force_execute (headless / already-approved runs).
                    "force_execute": bool(state.get("force_execute")),
                    "sub_tasks": None,  # re-decompose the focused directive
                    "seed_values": _sanitize_continue_seeds(state),
                    "last_failure": current_failure or state.get("last_failure"),
                    "execution_id": None,
                    "repeat_failure_count": repeat_failure_count,
                    "last_plan_op_ids": last_plan_op_ids,
                    "token_usage": new_token_usage,
                    "model_audit": [goal_audit] if goal_audit else [],
                }
        except Exception as exc:
            logger.error("Error during GOAP goal evaluation check: %s", exc)

        return {
            **tracking,
            "remaining_goal_facts": remaining,
            "response": get_terminal_response(),
            "goal_loop_decision": "done",
            "repeat_failure_count": repeat_failure_count,
            "token_usage": new_token_usage,
            "model_audit": [goal_audit] if goal_audit else [],
        }
    return goap_goal_node
