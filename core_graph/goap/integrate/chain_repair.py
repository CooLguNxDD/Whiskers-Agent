"""Deterministic collection→by-id chain repair for GOAP plans.

When seed pollution or dual independent goals produce get-before-list (or get
without a list producer), rewrite the plan so a collection op runs first and
the consumer binds its id param from that step.
"""
from __future__ import annotations

import re
from typing import Any

from core_graph.goap.derive import (
    _is_collection_op,
    _required_params,
    derive_action,
)


_STEPS_REF_RE = re.compile(r"^\$steps\[(\d+)\]")


def _id_like_required_params(candidate: dict) -> list[str]:
    """Required params that look like resource ids (sessionId, page_id, id)."""
    from core_graph.goap.integrate.seed_hygiene import is_id_like_param

    return [p for p in _required_params(candidate) if is_id_like_param(p)]


def _has_step_binding(step: dict, param: str) -> bool:
    """True when param is already bound to a prior step result."""
    ref = (step.get("arg_bindings") or {}).get(param)
    return isinstance(ref, str) and bool(_STEPS_REF_RE.match(ref))


def _fact_for_param(param: str) -> str:
    return f"have:{param}"


def _producer_field_for_fact(action, fact: str) -> str:
    return action.effect_field_map().get(fact, "id")


def _find_producer_in_plan(
    steps: list[dict],
    candidates_by_op: dict[str, dict],
    fact: str,
    *,
    before_idx: int | None = None,
) -> int | None:
    """Index of a plan step whose derived action produces ``fact``."""
    limit = before_idx if before_idx is not None else len(steps)
    for i in range(limit):
        op = steps[i].get("operation_id")
        cand = candidates_by_op.get(op) if op else None
        if not cand:
            # Build a minimal candidate from the step itself
            cand = {
                "operation_id": op,
                "plugin_id": steps[i].get("plugin_id", ""),
                "method": "CALL",
                "parameters": {},
            }
        action = derive_action(cand)
        if fact in action.effects:
            return i
    return None


def _find_producer_candidate(
    candidates: list[dict],
    fact: str,
    *,
    exclude_ops: set[str] | None = None,
) -> dict | None:
    """First candidate action that produces ``fact`` and is a collection op."""
    exclude = exclude_ops or set()
    for cand in candidates:
        op = cand.get("operation_id") or ""
        if op in exclude:
            continue
        action = derive_action(cand)
        if fact not in action.effects:
            continue
        plugin_id = cand.get("plugin_id") or ""
        if _is_collection_op(op, plugin_id) or fact in action.effects:
            # Prefer collection ops; also accept any producer of the fact
            if _is_collection_op(op, plugin_id):
                return cand
    # Fallback: any producer
    for cand in candidates:
        op = cand.get("operation_id") or ""
        if op in exclude:
            continue
        action = derive_action(cand)
        if fact in action.effects:
            return cand
    return None


def _reindex_step_refs(obj: Any, mapping: dict[int, int]) -> Any:
    """Rewrite $steps[i] references according to old→new index mapping."""
    if isinstance(obj, str):
        def _sub(m):
            old = int(m.group(1))
            new = mapping.get(old, old)
            return f"$steps[{new}]"

        return re.sub(r"\$steps\[(\d+)\]", _sub, obj)
    if isinstance(obj, list):
        return [_reindex_step_refs(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: _reindex_step_refs(v, mapping) for k, v in obj.items()}
    return obj


def _bind_consumer_to_producer(
    consumer: dict,
    producer_idx: int,
    param: str,
    field: str = "id",
) -> None:
    """Set arg_bindings + depends_on so consumer takes param from producer."""
    bindings = dict(consumer.get("arg_bindings") or {})
    bindings[param] = f"$steps[{producer_idx}].{field}"
    consumer["arg_bindings"] = bindings
    # Drop literal seed arg so binding wins
    args = dict(consumer.get("args") or {})
    args.pop(param, None)
    consumer["args"] = args
    deps = consumer.get("depends_on")
    if deps is None:
        consumer["depends_on"] = producer_idx
    elif isinstance(deps, int):
        consumer["depends_on"] = max(deps, producer_idx)
    elif isinstance(deps, list):
        if producer_idx not in deps:
            deps = list(deps) + [producer_idx]
        consumer["depends_on"] = deps


def repair_collection_consumer_chain(
    steps: list[dict],
    candidates: list[dict],
) -> list[dict]:
    """Ensure by-id consumers run after a collection producer that supplies their id.

    Mutates and returns a new step list (may reorder/insert). Safe no-op when
    the plan is already correctly chained.
    """
    if not steps:
        return steps

    candidates_by_op = {
        c["operation_id"]: c for c in (candidates or []) if c.get("operation_id")
    }
    # Work on a shallow-copied list of step dicts
    plan = [dict(s) for s in steps]
    # Deep-copy nested dicts we mutate
    for s in plan:
        if isinstance(s.get("arg_bindings"), dict):
            s["arg_bindings"] = dict(s["arg_bindings"])
        if isinstance(s.get("args"), dict):
            s["args"] = dict(s["args"])

    changed = True
    # Bound iterations: at most one insert/reorder per step
    for _ in range(max(4, len(plan) + 2)):
        if not changed:
            break
        changed = False
        for ci, consumer in enumerate(list(plan)):
            op = consumer.get("operation_id")
            cand = candidates_by_op.get(op) if op else None
            if not cand:
                continue
            id_params = _id_like_required_params(cand)
            if not id_params:
                continue
            # Skip pure collection ops that also happen to have optional id params
            if _is_collection_op(op, cand.get("plugin_id") or ""):
                continue

            for param in id_params:
                if _has_step_binding(consumer, param):
                    continue
                fact = _fact_for_param(param)

                # Producer already earlier in plan?
                prod_idx = _find_producer_in_plan(
                    plan, candidates_by_op, fact, before_idx=ci
                )
                if prod_idx is not None:
                    action = derive_action(
                        candidates_by_op.get(plan[prod_idx].get("operation_id"))
                        or {
                            "operation_id": plan[prod_idx].get("operation_id"),
                            "plugin_id": plan[prod_idx].get("plugin_id", ""),
                            "method": "CALL",
                            "parameters": {},
                        }
                    )
                    field = _producer_field_for_fact(action, fact)
                    _bind_consumer_to_producer(consumer, prod_idx, param, field)
                    plan[ci] = consumer
                    changed = True
                    continue

                # Producer later in plan → reorder
                later_idx = _find_producer_in_plan(
                    plan, candidates_by_op, fact, before_idx=None
                )
                # only consider indices after ci
                if later_idx is not None and later_idx > ci:
                    # Move producer before consumer
                    producer = plan.pop(later_idx)
                    # After pop, consumer may have shifted if later_idx < ci — but later > ci so ci unchanged
                    plan.insert(ci, producer)
                    # Rebuild index mapping for refs (simple full reindex of plan order)
                    # Old indices: everything from ci..later_idx-1 shifted +1
                    mapping = {}
                    for old in range(len(plan)):
                        if old < ci:
                            mapping[old] = old
                        elif old == later_idx:
                            mapping[old] = ci
                        elif ci <= old < later_idx:
                            mapping[old] = old + 1
                        else:
                            mapping[old] = old
                    plan = [_reindex_step_refs(s, mapping) for s in plan]
                    # Consumer is now at ci+1
                    new_ci = ci + 1
                    consumer = plan[new_ci]
                    prod_cand = candidates_by_op.get(plan[ci].get("operation_id")) or {
                        "operation_id": plan[ci].get("operation_id"),
                        "plugin_id": plan[ci].get("plugin_id", ""),
                        "method": "CALL",
                        "parameters": {},
                    }
                    field = _producer_field_for_fact(derive_action(prod_cand), fact)
                    _bind_consumer_to_producer(consumer, ci, param, field)
                    plan[new_ci] = consumer
                    changed = True
                    break  # restart outer scan after reorder

                # No producer in plan → insert from candidates
                if later_idx is None and prod_idx is None:
                    exclude = {s.get("operation_id") for s in plan if s.get("operation_id")}
                    prod_cand = _find_producer_candidate(
                        candidates, fact, exclude_ops=exclude
                    )
                    # Also allow producer already not in plan even if op not excluded wrong
                    if prod_cand is None:
                        prod_cand = _find_producer_candidate(candidates, fact, exclude_ops=set())
                        if prod_cand and prod_cand.get("operation_id") in {
                            s.get("operation_id") for s in plan
                        }:
                            # Producer is in plan but didn't match effects — skip
                            prod_cand = None
                    if prod_cand is None:
                        continue
                    # Avoid inserting if already present (matched later path missed)
                    existing = next(
                        (
                            i
                            for i, s in enumerate(plan)
                            if s.get("operation_id") == prod_cand.get("operation_id")
                        ),
                        None,
                    )
                    if existing is not None:
                        if existing > ci:
                            # Will be handled as reorder on next pass
                            continue
                        field = _producer_field_for_fact(derive_action(prod_cand), fact)
                        _bind_consumer_to_producer(consumer, existing, param, field)
                        plan[ci] = consumer
                        changed = True
                        continue

                    insert_step = {
                        "operation_id": prod_cand.get("operation_id"),
                        "plugin_id": prod_cand.get("plugin_id"),
                        "is_fast_path": bool(prod_cand.get("is_fast_path")),
                        "intent": prod_cand.get("description") or "List (chain repair)",
                        "args": {},
                        "arg_bindings": {},
                        "depends_on": None,
                        "model": prod_cand.get("model"),
                    }
                    plan.insert(ci, insert_step)
                    # Shift all indices >= ci by +1 for existing refs
                    mapping = {i: (i + 1 if i >= ci else i) for i in range(len(plan) - 1)}
                    # Remap steps that were already in plan (skip the newly inserted at ci)
                    for i in range(len(plan)):
                        if i == ci:
                            continue
                        plan[i] = _reindex_step_refs(plan[i], mapping)
                    new_ci = ci + 1
                    consumer = plan[new_ci]
                    field = _producer_field_for_fact(derive_action(prod_cand), fact)
                    _bind_consumer_to_producer(consumer, ci, param, field)
                    plan[new_ci] = consumer
                    candidates_by_op[prod_cand["operation_id"]] = prod_cand
                    changed = True
                    break
            if changed:
                break

    return plan
