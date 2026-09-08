from typing import Iterable, Optional

from core_graph.goap.derive import _ID_ALIASES, derive_actions
from core_graph.goap import planner
from core_graph.goap.planner import GoapPlan
from core_graph.goap.world_state import WorldState
from core_graph.goap.integrate._shared import _SEARCH_PARAM_NAMES
from core_graph.goap.integrate.seed_hygiene import (
    enrich_instruction_value,
    is_bare_search_followup,
    is_id_like_param,
    is_instruction_param,
    is_weak_instruction_value,
    looks_like_real_id,
    resolve_search_query,
    sanitize_seed_values,
)
from utils.server_config import GOAP_MAX_STEPS, GOAP_MAX_EXPANSIONS


def _enrich_instruction_fields(step: dict, user_query: str | None) -> None:
    """Expand bare role/label seeds in prompt-like args and for_each_values.

    Fan-out of Jules roles (frontend-b, docs, …) must not ship those labels as
    the entire ``prompt`` — wrap them with the original user request so the
    agent receives a real task brief.
    """
    if not user_query or not str(user_query).strip():
        return
    args = step.setdefault("args", {})
    for p, v in list(args.items()):
        if is_instruction_param(p) and is_weak_instruction_value(v):
            args[p] = enrich_instruction_value(v, user_query)

    bindings = step.get("arg_bindings") or {}
    fe_vals = step.get("for_each_values")
    if not isinstance(fe_vals, list) or not fe_vals:
        return
    # Enrich when the fanned $item feeds an instruction param
    instr_bound = any(
        is_instruction_param(p) and ref == "$item"
        for p, ref in bindings.items()
    )
    if not instr_bound:
        # Homogeneous collapse leaves for_each_values with prompt popped from args
        # but binding still set; also cover binding-less residual cases where the
        # only free-text seed path was instruction-shaped.
        return
    step["for_each_values"] = [
        enrich_instruction_value(v, user_query) if is_weak_instruction_value(v) else v
        for v in fe_vals
    ]


def build_seed_world(
    seed_facts: Iterable[str],
    working_memory: dict | None,
    seed_values: dict | None = None,
) -> WorldState:
    """
    Construct a WorldState using seed facts, working memory keys, and literal seed_values.

    LLM seed_values are sanitized so weak free-text id params (e.g. sessionId="Jules")
    do not falsely satisfy have:{param} preconditions and skip collection→by-id chains.
    """
    seen: set[str] = set()
    facts: list[str] = []
    for fact in seed_facts:
        if fact not in seen:
            facts.append(fact)
            seen.add(fact)
        if fact.startswith("have:"):
            k = fact[len("have:"):]
            if k.lower() in _ID_ALIASES:
                if "have:id" not in seen:
                    facts.append("have:id")
                    seen.add("have:id")
    for k in (working_memory or {}):
        fact = f"have:{k}"
        if fact not in seen:
            facts.append(fact)
            seen.add(fact)
        if k.lower() in _ID_ALIASES:
            if "have:id" not in seen:
                facts.append("have:id")
                seen.add("have:id")
    # Literal params from goal extraction satisfy GOAP preconditions (have:query, etc.).
    # Drop weak id-like seeds that were not already trusted via working_memory.
    safe_seeds = sanitize_seed_values(seed_values, working_memory)
    for k in safe_seeds:
        fact = f"have:{k}"
        if fact not in seen:
            facts.append(fact)
            seen.add(fact)
        if k.lower() in _ID_ALIASES:
            if "have:id" not in seen:
                facts.append("have:id")
                seen.add("have:id")
    return WorldState(facts)


def goap_plan_from_goal(
    goal: list[str],
    seed_facts: list[str],
    working_memory: dict | None,
    candidates: list[dict],
    *,
    seed_values: dict | None = None,
    max_expansions: int = GOAP_MAX_EXPANSIONS,
) -> Optional[GoapPlan]:
    """
    Derive actions from candidates, build a seed world state, and run the GOAP planner.
    """
    if not goal:
        return None

    actions = derive_actions(candidates)
    safe_seeds = sanitize_seed_values(seed_values, working_memory)
    world = build_seed_world(seed_facts, working_memory, safe_seeds)
    return planner.plan(goal, world, actions, max_steps=GOAP_MAX_STEPS, max_expansions=max_expansions)


def fill_literal_args(
    steps: list[dict],
    candidates: list[dict],
    seed_values: dict | None,
    working_memory: dict | None,
    *,
    user_query: str | None = None,
    history=None,
    last_summary: str | None = None,
) -> None:
    """
    Fill missing required step arguments from seed values or working memory.
    Last-resort: for a leaf single free-text/search param, fall back to a
    topical query (never a bare "search up!" imperative).

    List-valued seed_values (fan-out drivers) are never injected as raw step args;
    they are handled exclusively by inject_literal_list_fanout → for_each_values.

    Weak free-text id seeds (sessionId="Jules") are filtered so they cannot
    short-circuit collection→by-id chains.

    Instruction params (``prompt``, …) never receive the aggressive search
    fallback. Bare role/label seeds are enriched from ``user_query`` so agent
    session tools (e.g. julescreate_session) get a real task brief.
    """
    candidates_by_op = {c["operation_id"]: c for c in candidates if "operation_id" in c}
    from core_graph.goap.derive import _required_params, _schema_param_names

    safe_seeds = sanitize_seed_values(seed_values, working_memory)
    fallback_query = resolve_search_query(
        user_query, history=history, last_summary=last_summary
    )

    for step in steps:
        op_id = step.get("operation_id")
        if not op_id:
            continue
        cand = candidates_by_op.get(op_id)
        if not cand:
            continue
        req_params = _required_params(cand)
        for p in req_params:
            if "args" not in step:
                step["args"] = {}
            if p in step["args"]:
                continue
            if p in (step.get("arg_bindings") or {}):
                continue
            if safe_seeds and p in safe_seeds:
                v = safe_seeds[p]
                # List-valued seed_values drive inject_literal_list_fanout (for_each_values),
                # NOT step args. Never inject a raw list as a scalar param value.
                if isinstance(v, list):
                    continue
                # Weak instruction labels stay unfilled here when no user_query
                # enrichment is possible; enrichment pass below upgrades them.
                if is_instruction_param(p) and is_weak_instruction_value(v) and not user_query:
                    continue
                if p in _SEARCH_PARAM_NAMES and is_bare_search_followup(v):
                    continue
                step["args"][p] = v
            elif working_memory and p in working_memory:
                step["args"][p] = working_memory[p]

        # Deterministic last-resort for common single free-text/search params
        # Only when: leaf (no arg_bindings for p), still unfilled required p,
        # p is a recognized search/text name, and it is the *only* still-unfilled
        # required free-text param for this step (avoid ambiguity).
        # Never applies to instruction params (prompt/instruction/task).
        # Never dumps a bare "search up!" — resolve against prior-turn topic.
        if fallback_query:
            unfilled_free_text = []
            for p in req_params:
                if p in step.get("args", {}):
                    continue
                if p in (step.get("arg_bindings") or {}):
                    continue
                if is_instruction_param(p):
                    continue
                if p in _SEARCH_PARAM_NAMES:
                    unfilled_free_text.append(p)
            if len(unfilled_free_text) == 1:
                p = unfilled_free_text[0]
                if p not in step.get("args", {}) and not is_bare_search_followup(fallback_query):
                    step["args"][p] = fallback_query

        _enrich_instruction_fields(step, user_query)

    # Defensive pass: always apply any seed_values for params that are still
    # unfilled and not arg_bound. This rescues cases where the exact
    # candidates_by_op lookup failed (exotic proxy names like
    # "proxy_Notion-AndrewDev-2__notion-search", case/qualifier diffs, or the
    # cand snapshot at fill time lacked the "parameters" shape used for
    # _required_params). The LLM goal extractor already validated the literal
    # against a required param it saw; we must not drop it.
    #
    # Guards:
    #  - Skip list-valued entries: those are fan-out drivers (for_each_values),
    #    never scalar arg values — injecting them causes API proto errors like
    #    "title is not repeating, cannot start list".
    #  - Skip params already bound via $item (the step is fanned; each item
    #    provides the value, not the seed list).
    if safe_seeds:
        for step in steps:
            step.setdefault("args", {})
            cand = candidates_by_op.get(step.get("operation_id"))
            allowed = _schema_param_names(cand) if cand else None  # None => unknown, allow all
            # Params already used as the per-item binding on this fanned step
            fanout_bound = {
                p for p, ref in (step.get("arg_bindings") or {}).items()
                if isinstance(ref, str) and ref == "$item"
            }
            for p, v in safe_seeds.items():
                if p in step["args"] or p in (step.get("arg_bindings") or {}):
                    continue
                if allowed is not None and p not in allowed:
                    continue
                # Never inject a list as a scalar arg — list seeds are fan-out drivers.
                if isinstance(v, list):
                    continue
                # Skip params already satisfied per-item by the fan-out mechanism.
                if p in fanout_bound:
                    continue
                # Extra guard: never re-inject weak id-like values (defense in depth)
                if is_id_like_param(p) and not looks_like_real_id(v) and p not in (working_memory or {}):
                    continue
                if is_instruction_param(p) and is_weak_instruction_value(v):
                    if user_query:
                        step["args"][p] = enrich_instruction_value(v, user_query)
                    continue
                if p in _SEARCH_PARAM_NAMES and is_bare_search_followup(v):
                    continue
                step["args"][p] = v
            _enrich_instruction_fields(step, user_query)

    # Steps that only had for_each_values (no candidate match above) still need
    # instruction enrichment when user_query is available.
    if user_query:
        for step in steps:
            _enrich_instruction_fields(step, user_query)
