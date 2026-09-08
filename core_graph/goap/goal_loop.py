"""Goal-loop planning orchestration helpers.

This module provides pure helper functions for managing bounded autonomous goal-loops,
including per-turn state resets, folding step results into working memory, parsing
goal checks, and making iteration/loop decisions.
"""

from utils.server_config import MAX_ITERATIONS as DEFAULT_MAX_ITERATIONS

# Bounded cross-round evidence log (round_summary → goap_goal verifier + final summary).
MAX_RESEARCH_NOTES = 10
RESEARCH_NOTE_CHARS = 1500
RESEARCH_NOTES_BUDGET = 12_000


def append_research_note(
    notes: list | None,
    iteration: int,
    directive: str,
    ops: list | None,
    step_results: list | None,
    *,
    max_notes: int = MAX_RESEARCH_NOTES,
    per_note_chars: int = RESEARCH_NOTE_CHARS,
) -> list[dict]:
    """Append one bounded per-round note; drop oldest when over ``max_notes``.

    Deterministic (no LLM). ``findings`` is a char-capped serialization of the
    round's ``step_results`` so the verifier/final summary retain substance
    after ``goap_goal`` clears ``step_results`` on continue.
    """
    import json

    findings = json.dumps(step_results or [], default=str)
    if len(findings) > per_note_chars:
        findings = findings[:per_note_chars]
    note = {
        "iteration": int(iteration) if iteration is not None else 0,
        "directive": str(directive or "")[:500],
        "ops": [o for o in (ops or []) if isinstance(o, str) and o],
        "findings": findings,
    }
    out = [n for n in (notes or []) if isinstance(n, dict)]
    out.append(note)
    if max_notes > 0 and len(out) > max_notes:
        out = out[-max_notes:]
    return out


def format_research_notes(
    notes: list | None,
    budget: int = RESEARCH_NOTES_BUDGET,
) -> str:
    """Render research notes oldest→newest into a prompt-ready string (char-bounded).

    When over budget, keep the newest tail so recent rounds stay complete.
    """
    if not notes:
        return ""
    parts: list[str] = []
    for n in notes:
        if not isinstance(n, dict):
            continue
        it = n.get("iteration", "?")
        directive = n.get("directive") or ""
        ops = n.get("ops") or []
        findings = n.get("findings") or ""
        parts.append(
            f"[round {it}] directive={directive}\nops={ops}\nfindings={findings}"
        )
    if not parts:
        return ""
    text = "\n\n".join(parts)
    if budget > 0 and len(text) > budget:
        text = text[-budget:]
    return text


def _strip_op_prefix(op_id: str) -> str:
    """Return the operation_id with any proxy ``__`` prefix stripped (last segment)."""
    if not op_id:
        return ""
    return op_id.rpartition("__")[-1] if "__" in op_id else op_id


def evaluate_goal_facts(
    goal_facts: list[str] | None,
    world_state,
    executed_op_ids: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Return (achieved, remaining) sub-goals given the world state and executed ops.

    A ``have:X`` fact is achieved when ``world_state.has("have:X")`` (X is in folded
    working memory). A ``did:OP`` fact is achieved when OP — matched raw or with the
    proxy ``__`` prefix stripped on both sides — appears in ``executed_op_ids``.
    """
    achieved: list[str] = []
    remaining: list[str] = []
    if not goal_facts:
        return achieved, remaining

    executed = set(executed_op_ids or [])
    executed_stripped = {_strip_op_prefix(o) for o in executed}

    for fact in goal_facts:
        if not isinstance(fact, str):
            continue
        if fact.startswith("did:"):
            op = fact[len("did:"):]
            if op in executed or _strip_op_prefix(op) in executed_stripped:
                achieved.append(fact)
            else:
                remaining.append(fact)
        elif fact.startswith("have:"):
            if world_state.has(fact):
                achieved.append(fact)
            else:
                remaining.append(fact)
        else:
            # Unknown fact shape — treat as remaining (cannot prove satisfaction).
            remaining.append(fact)

    return achieved, remaining


def world_from_memory(working_memory: dict | None):
    """Build a WorldState of ``have:<key>`` facts from working_memory keys."""
    from core_graph.goap.integrate import build_seed_world
    return build_seed_world([], working_memory)


def build_goal_directive(remaining_goal_facts: list[str] | None, next_hint: str = "") -> str:
    """Build a focused next-turn directive listing the remaining sub-goals.

    Falls back to ``next_hint`` alone when no structured remaining facts are present.
    """
    facts = [f for f in (remaining_goal_facts or []) if isinstance(f, str)]
    if not facts:
        return next_hint or ""

    lines = []
    for fact in facts:
        if fact.startswith("did:"):
            lines.append(f"- perform {fact[len('did:'):]}")
        elif fact.startswith("have:"):
            lines.append(f"- obtain {fact[len('have:'):]}")
        else:
            lines.append(f"- {fact}")
    directive = "Complete the remaining sub-goals:\n" + "\n".join(lines)
    if next_hint:
        directive += f"\n\nNext step hint: {next_hint}"
    return directive


_FAILED_STATUSES = {"error", "partial", "need_input"}


def _step_failed(res) -> bool:
    """True when a step_result entry represents a failed/unexecuted step —
    used so a failed op can't falsely satisfy its own ``did:<op>`` goal fact."""
    if not isinstance(res, dict):
        return False
    if res.get("status") in _FAILED_STATUSES:
        return True
    if "error" in res:
        return True
    return False


def accumulate_executed_ops(
    prev: list[str] | None,
    plan: list[dict] | None,
    step_results: list[dict] | None,
) -> list[str]:
    """Return the cumulative list of *successfully executed* operation_ids
    (order-preserving, unique).

    Folds the prior accumulator with op_ids from the just-executed plan steps
    (skipping any step whose corresponding, positionally-aligned step_results
    entry is missing or failed) and any ``operation_id`` carried on non-failed
    step results. A failed op must not satisfy its own ``did:<op>`` goal fact.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _add(op):
        if isinstance(op, str) and op and op not in seen:
            seen.add(op)
            out.append(op)

    results = step_results or []
    for op in (prev or []):
        _add(op)
    for i, step in enumerate(plan or []):
        if not isinstance(step, dict):
            continue
        res = results[i] if i < len(results) else None
        if res is None or _step_failed(res):
            continue
        _add(step.get("operation_id"))
    for res in results:
        if isinstance(res, dict) and not _step_failed(res):
            _add(res.get("operation_id"))
    return out


def collect_fetched_ids(step_results: list | None, candidate_ids) -> list[str]:
    """Return the distinct candidate ids that appear in by-id fetch result envelopes.

    Distinguishes fan-out fetch output (top-level ``results`` list) and single-page
    fetch envelopes (``data`` dict that is *not* a search results wrapper) from the
    search step's own ``data.results`` candidate listing. Matching is dash-insensitive
    and substring-based: a fetched page often echoes its id only inside a URL and with
    dashes stripped (e.g. ``…/p/3422783caabd8138…`` vs the dashed search id
    ``3422783c-aabd-8138-…``), so we normalize both sides before comparing.
    """
    cand = [str(c) for c in (candidate_ids or []) if c]
    if not cand:
        return []

    strings: list[str] = []

    def _collect(obj) -> None:
        if isinstance(obj, dict):
            for v in obj.values():
                _collect(v)
        elif isinstance(obj, list):
            for v in obj:
                _collect(v)
        elif isinstance(obj, str):
            strings.append(obj)

    for res in (step_results or []):
        if not isinstance(res, dict):
            continue
        top = res.get("results")
        if isinstance(top, list):
            # Fan-out fetch output: each entry is a per-item fetch envelope.
            _collect(top)
            continue
        data = res.get("data")
        # Single-page fetch: data is a page dict, not a {"results": [...]} search wrapper.
        if isinstance(data, dict) and not isinstance(data.get("results"), list):
            _collect(data)

    norm_blobs = [s.replace("-", "").lower() for s in strings]
    found: list[str] = []
    seen: set[str] = set()
    for c in cand:
        if c in seen:
            continue
        cn = c.replace("-", "").lower()
        if cn and any(cn in blob for blob in norm_blobs):
            seen.add(c)
            found.append(c)
    return found


def reset_turn_fragment(state: dict) -> dict:
    """Return the per-turn reset dict with transient fields cleared and memory kept.

    Accepts the current state dictionary, preserves the goal and working memory,
    and resets planning-related values.
    """
    # Preserve max_iterations and working_memory if present, else fallback
    max_iter = state.get("max_iterations") or DEFAULT_MAX_ITERATIONS
    working_mem = dict(state.get("working_memory") or {})

    return {
        "plan": [],
        "candidate_pool": state.get("candidate_pool") or [],
        "candidates": [],
        "current_step_index": 0,
        "step_results": [],
        "response": None,
        "retry_count": 0,
        "selected": None,
        "parallel_groups": [],
        "yaml_workflow": "",
        "instruction_set": [],
        "workflow_plan_id": None,
        "payload": None,
        "resolved_args": None,
        "unresolved_required": [],
        "summary": None,
        "iterations": 0,
        "max_iterations": max_iter,
        "working_memory": working_mem,
        "pre_round_memory": None,
        "goal": state.get("goal"),
        # Stamped once per user turn from the incoming (not-yet-rewritten) user_query.
        # The goal-loop `continue` path re-enters at decompose/embedder (skipping
        # turn_init/reset_turn_fragment), so this is never overwritten mid-loop even as
        # `user_query` itself gets rewritten to successive focused sub-directives.
        "original_query": state.get("user_query"),
        # Cross-round evidence log: round_summary appends; goap_goal continue never
        # clears it; only a new user turn (this reset) empties it.
        "research_notes": [],
        # MinIO artifact refs from this turn only; re-accumulate on continue rounds
        # via round_summary (not cleared there).
        "artifacts": [],
        "replan_context": [],
        "last_failure": None,
        # Preserve any GOAP seed_values from the prior turn so a resume/re-plan
        # (new embedder on next_hint) can still feed fill_literal_args even if
        # the new candidates list is smaller or the op_id match is tricky for
        # exotic proxy names.
        "seed_values": state.get("seed_values"),
        "token_usage": state.get("token_usage") or {},
        # Fresh structured goal tracking per user turn. The goal-loop `continue`
        # path re-enters at the embedder (skipping turn_init), so these persist
        # across iterations of the same goal and only reset on a new user turn.
        "goal_facts": [],
        "remaining_goal_facts": [],
        "achieved_facts": [],
        "executed_op_ids": [],
        "fanout_target": None,
        "goal_loop_decision": None,
        "retriage_count": 0,
        "retriage_context": None,
        # Decompose-first fields reset per user turn so stale sub-tasks never
        # drive retrieval for a new request.
        "sub_tasks": None,
        "decompose_intent": None,
        "decompose_seed_values": {},
    }


def _derive_resource_from_route(route_str: str) -> str:
    if not route_str:
        return ""
    import re
    # Strip method prefix
    route_str = re.sub(r'^(GET|POST|PUT|PATCH|DELETE|CALL)\s+', '', route_str, flags=re.IGNORECASE)
    route_str = route_str.strip()
    if not route_str:
        return ""
    
    # If it's a path (contains /), get the last non-parameter segment
    if "/" in route_str:
        segments = [seg for seg in route_str.split("/") if seg]
        static_segments = [seg for seg in segments if not (seg.startswith("{") or seg.startswith(":"))]
        if static_segments:
            base_word = static_segments[-1]
        else:
            base_word = segments[-1]
    else:
        base_word = route_str

    # Handle proxy operations with __
    if "__" in base_word:
        base_word = base_word.rpartition("__")[-1]

    # Strip verbs from start/end
    verbs = {
        "create", "get", "list", "update", "delete", "add", "send",
        "fetch", "find", "search", "post", "put", "patch"
    }
    base_lower = base_word.lower()
    noun = ""
    for verb in sorted(verbs, key=len, reverse=True):
        if base_lower.startswith(verb):
            verb_len = len(verb)
            if len(base_word) == verb_len:
                noun = ""
                break
            next_char = base_word[verb_len]
            if next_char in ("_", "-") or next_char.isupper():
                noun = base_word[verb_len:]
                noun = noun.lstrip("_-")
                break
        if base_lower.endswith(verb):
            verb_len = len(verb)
            if len(base_word) == verb_len:
                noun = ""
                break
            prev_char = base_word[-verb_len - 1]
            if prev_char in ("_", "-") or base_word[-verb_len].isupper():
                noun = base_word[:-verb_len]
                noun = noun.rstrip("_-")
                break

    if not noun:
        noun = base_word

    noun_lower = noun.lower().replace("-", "_")
    if noun_lower.endswith("s"):
        noun_lower = noun_lower[:-1]
    return noun_lower


def fold_working_memory(working_memory: dict, step_results: list) -> dict:
    """Return a NEW dict updated with id-like entities found in step_results.

    Scans the top level and one level under 'data' and 'result' of each step result,
    copying any scalar ending with '_id' or updating keys already present in working_memory.
    Also scans one level into list envelopes (results, items, data.results, data) and promotes
    the first element's id/url/id-like fields.
    """
    from core_graph.node.helpers import _ID_ALIASES

    new_memory = dict(working_memory)

    for result in step_results:
        if not isinstance(result, dict):
            continue

        # Collect candidate dictionaries to scan (top-level and one level under data/result)
        dicts_to_scan = [result]
        for sub_key in ("data", "result"):
            sub_dict = result.get(sub_key)
            if isinstance(sub_dict, dict):
                dicts_to_scan.append(sub_dict)

        # Update matching keys with scalar values, or refresh existing memory keys
        for d in dicts_to_scan:
            for k, v in d.items():
                if isinstance(v, (str, int, float, bool)) and not isinstance(v, type(None)):
                    if k.endswith("_id") or k in working_memory:
                        new_memory[k] = v
                elif k in working_memory:
                    new_memory[k] = v

        # Scan one level into list envelopes (results, items, data.results, data if it is a list)
        list_envelopes = []
        for key in ("results", "items", "data"):
            val = result.get(key)
            if isinstance(val, list):
                list_envelopes.append((key, val))
        data_dict = result.get("data")
        if isinstance(data_dict, dict):
            for key in ("results", "items"):
                val = data_dict.get(key)
                if isinstance(val, list):
                    list_envelopes.append((f"data.{key}", val))

        for _env_path, lst in list_envelopes:
            if not lst:
                continue
            first_elem = lst[0]
            if isinstance(first_elem, dict):
                route_str = result.get("route") or result.get("operation_id") or ""
                resource = _derive_resource_from_route(route_str)

                for k, v in first_elem.items():
                    if isinstance(v, (str, int, float, bool)) and not isinstance(v, type(None)):
                        k_lower = k.lower()
                        is_id_like = k_lower in _ID_ALIASES or k_lower.endswith("_id") or k_lower == "url"
                        if is_id_like:
                            # Promoted to bare key
                            new_memory[k] = v

                            # Promote to resource-qualified key if resource is known (and key is not url)
                            if resource and k_lower != "url":
                                new_memory[f"{resource}_id"] = v

                            # Promote to any qualified keys in working_memory (and key is not url)
                            if k_lower != "url":
                                for wm_k in working_memory:
                                    if wm_k.endswith("_id"):
                                        prefix = wm_k[:-3].lower()
                                        if (prefix in resource or
                                            resource in prefix or
                                            prefix in route_str.lower() or
                                            (prefix == "page" and "notion" in route_str.lower())):
                                            new_memory[wm_k] = v

                # Harvest ALL id-like values from every element to support fan-out / multi-fetch.
                # The singular keys above give compat; plural *_ids keys let the planner and
                # step_resolver reference the full collection for "fetch N / fetch all" requests.
                all_ids_here: list[str] = []
                seen_here: set[str] = set()
                for elem in lst:
                    if not isinstance(elem, dict):
                        continue
                    for k, v in elem.items():
                        k_lower = k.lower()
                        if (
                            isinstance(v, str) and v
                            and (k_lower in _ID_ALIASES or k_lower.endswith("_id") or k_lower == "page_id")
                        ):
                            if v not in seen_here:
                                seen_here.add(v)
                                all_ids_here.append(v)
                if all_ids_here:
                    new_memory["ids"] = all_ids_here
                    if resource:
                        new_memory[f"{resource}_ids"] = all_ids_here
                    if "notion" in route_str.lower():
                        new_memory["page_ids"] = all_ids_here
                        new_memory["notion_ids"] = all_ids_here

    return new_memory


def parse_goal_check(parsed: dict | None) -> tuple[bool, str, str]:
    """Return (done, reason, next_hint) coerced and defaulted from a parsed LLM response.

    Handles None or missing fields gracefully to guarantee a safe tuple return.
    """
    if not parsed or not isinstance(parsed, dict):
        return False, "", ""

    done = bool(parsed.get("done"))
    reason = str(parsed.get("reason") or "")
    next_hint = str(parsed.get("next_hint") or "")

    return done, reason, next_hint


def decide_goal_loop(goal, iterations, max_iterations, done) -> str:
    """Return 'done' if the goal is completed or iteration limit reached, else 'continue'.

    Evaluates whether the loop has finished or needs to perform another planning iteration.
    """
    if not goal or done or iterations >= max_iterations:
        return "done"
    return "continue"
