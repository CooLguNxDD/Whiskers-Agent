import re

_COLLECTION_OP_VERBS = ("search", "list", "find", "query", "all")
_BY_ID_OP_VERBS = ("fetch", "get", "read", "retrieve", "show", "detail", "view", "load")
_STEP_REF_RE = __import__("re").compile(r"^\$steps\[(\d+)\](?:\.(.+))?$")

# Word → integer for free-text count detection ("pick five", "both", "a couple").
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "both": 2, "couple": 2, "few": 3, "several": 3,
}


def detect_fanout_count(user_query: str | None) -> int | None:
    """Return the target fan-out count parsed from the user query, else None.

    Recognizes explicit digits (``pick 5``, ``fetch 5 pages``) and number words
    (``five``, ``both``, ``a couple``). Returns None for unbounded phrasing
    (``all``, ``each``) — the caller then fans out over everything available.
    """
    if not user_query:
        return None
    import re
    q = user_query.lower()
    # Prefer explicit digits (most common: "pick 5", "5 pages"); cap to a sane range.
    for m in re.findall(r"\b(\d{1,3})\b", q):
        n = int(m)
        if 1 <= n <= 100:
            return n
    # Fall back to number words.
    for w, n in _NUM_WORDS.items():
        if re.search(rf"\b{w}\b", q):
            return n
    return None


def _strip_op_prefix(op_id: str) -> str:
    """Return the operation_id with any proxy ``__`` prefix stripped (last segment)."""
    if not op_id:
        return ""
    return op_id.rpartition("__")[-1] if "__" in op_id else op_id


def _id_param_of(step: dict) -> str | None:
    """Return the id-like parameter name for a by-id consumer step, if any.

    Prefers an id-like key already in arg_bindings, then args, defaulting to 'id'
    for recognized by-id ops (fetch/get/read/…)."""
    for src in (step.get("arg_bindings") or {}, step.get("args") or {}):
        for param in src:
            p = param.lower()
            if p in {"id", "page_id", "notion_id"} or p.endswith("_id"):
                return param
    op = (step.get("operation_id") or "").lower()
    if any(v in op for v in _BY_ID_OP_VERBS):
        return "id"
    return None


def _find_fetch_step(steps: list[dict]) -> dict | None:
    """Return the last by-id consumer step in the plan (the fetch target), if any."""
    for step in reversed(steps):
        op = (step.get("operation_id") or "").lower()
        if any(v in op for v in _BY_ID_OP_VERBS) or _id_param_of(step):
            return step
    return None


def _normalize_item_bindings(step: dict) -> None:
    """Rewrite producer-referencing arg_bindings to per-item ($item.<field>) form.

    When a step fans out over ``$steps[i].results``, any binding that still points at
    ``$steps[i].<field>`` would resolve to the *first* result for every item. Rewrite
    such bindings to ``$item.<field>`` so each fan-out item binds its own value."""
    fe = step.get("for_each")
    if not (isinstance(fe, str) and fe.startswith("$steps[")):
        return
    m = _STEP_REF_RE.match(fe.strip())
    prod_idx = int(m.group(1)) if m else None
    bindings = step.get("arg_bindings") or {}
    out = {}
    changed = False
    for param, ref in bindings.items():
        rm = _STEP_REF_RE.match(ref.strip()) if isinstance(ref, str) else None
        if rm and (prod_idx is None or int(rm.group(1)) == prod_idx):
            out[param] = f"$item.{rm.group(2) or 'id'}"
            changed = True
        else:
            out[param] = ref
    if changed:
        step["arg_bindings"] = out


def _inject_defensive_fanout(
    steps: list[dict],
    working_memory: dict | None = None,
    limit: int | None = None,
) -> None:
    """Inject for_each + $item bindings for search→fetch chains lacking explicit fan_out.

    Priority order:
      0. A goal-loop ``pending_fetch_ids`` queue (replan-for-remaining) → fan the
         fetch step over exactly those ids.
      1. Multi-step search→fetch chain → for_each over the producer's results list.
      2. Single-step fetch with plural ids already harvested into working_memory.

    ``limit`` caps the fan-out width (e.g. "pick 5"). Modifies steps in place;
    each case is a no-op when the consumer already has for_each/for_each_values.
    """
    _ID_PLURAL_KEYS = ("pending_fetch_ids", "page_ids", "notion_ids", "ids")
    wm = working_memory or {}

    def _cap(lst: list) -> list:
        return lst[:limit] if (isinstance(limit, int) and limit > 0) else lst

    # Case 0: explicit replan-for-remaining queue from the goap_goal node.
    pending = wm.get("pending_fetch_ids")
    if isinstance(pending, list) and pending:
        target = _find_fetch_step(steps)
        if target is not None and not target.get("for_each_values"):
            id_param = _id_param_of(target) or "id"
            target["for_each_values"] = _cap(list(pending))
            target["arg_bindings"] = {**(target.get("arg_bindings") or {}), id_param: "$item"}
            target.pop("for_each", None)
            return

    # Case 1: multi-step search→fetch chain — inject for_each referencing producer results
    for i in range(len(steps) - 1):
        producer = steps[i]
        consumer = steps[i + 1]
        if consumer.get("for_each") or consumer.get("for_each_values"):
            continue
        prod_op = (producer.get("operation_id") or "").lower()
        if not any(v in prod_op for v in _COLLECTION_OP_VERBS):
            continue
        bindings = consumer.get("arg_bindings") or {}
        id_param = None
        for param, ref in bindings.items():
            if isinstance(ref, str):
                m = _STEP_REF_RE.match(ref.strip())
                if m and int(m.group(1)) == i:
                    id_param = param
                    break
        if id_param is None:
            continue
        # Producer results list — try .results first, fall back to bare step reference
        consumer["for_each"] = f"$steps[{i}].results"
        # Rewrite id binding to extract .id from each dict item in the results list
        consumer["arg_bindings"] = {**bindings, id_param: "$item.id"}
        if isinstance(limit, int) and limit > 0:
            consumer["for_each_limit"] = limit

    # Case 2: single-step fetch with id param and plural ids already in working_memory
    # (subsequent goal-loop iterations after the prior search folded all ids)
    if wm:
        for step in steps:
            if step.get("for_each") or step.get("for_each_values"):
                continue
            bindings = step.get("arg_bindings") or {}
            # Only when an id-like param is referenced via a $steps binding (already resolved)
            id_param = None
            for param, ref in bindings.items():
                p_lower = param.lower()
                if p_lower in {"id", "page_id", "notion_id"} or p_lower.endswith("_id"):
                    id_param = param
                    break
            if id_param is None:
                continue
            for plural_key in _ID_PLURAL_KEYS:
                ids_list = wm.get(plural_key)
                if isinstance(ids_list, list) and len(ids_list) > 1:
                    step["for_each_values"] = _cap(list(ids_list))
                    step["arg_bindings"] = {**bindings, id_param: "$item"}
                    break


def inject_literal_list_fanout(
    steps: list[dict],
    seed_values: dict | None,
    candidates: list[dict],
    limit: int | None = None,
) -> None:
    """Inject for_each_values for steps where seed_values contains literal lists.

    Mutates steps in place.
    """
    if not steps or not seed_values:
        return

    def _cap(lst: list) -> list:
        return lst[:limit] if (isinstance(limit, int) and limit > 0) else lst

    candidates_by_op = {c["operation_id"]: c for c in candidates if "operation_id" in c}
    from core_graph.goap.derive import _schema_param_names

    for p, v in seed_values.items():
        if not isinstance(v, list) or len(v) <= 1:
            continue

        for step in steps:
            op_id = step.get("operation_id")
            if not op_id:
                continue
            cand = candidates_by_op.get(op_id)
            if not cand:
                continue
            if p not in _schema_param_names(cand):
                continue
            if "for_each" in step or "for_each_values" in step:
                continue

            step["for_each_values"] = _cap(v)
            step.setdefault("arg_bindings", {})[p] = "$item"
            step.setdefault("args", {}).pop(p, None)
            if isinstance(limit, int) and limit > 0:
                step["for_each_limit"] = limit


def collapse_homogeneous_fanout(
    steps: list[dict],
    limit: int | None = None,
) -> list[dict]:
    """Collapse multiple homogeneous steps into a single fanned-out step.

    If no collapse applies, returns the plan unchanged.
    """
    if not steps or len(steps) < 2:
        return steps

    from collections import defaultdict
    op_to_steps = defaultdict(list)
    for idx, step in enumerate(steps):
        op_to_steps[step.get("operation_id")].append((idx, step))

    def are_homogeneous_except_p(s1: dict, s2: dict, p: str) -> bool:
        """Check if two steps are identical except for parameter p."""
        if s1.get("operation_id") != s2.get("operation_id"):
            return False
        if "for_each" in s1 or "for_each_values" in s1 or "for_each" in s2 or "for_each_values" in s2:
            return False

        other_keys = set(s1.keys()) | set(s2.keys())
        other_keys.discard("args")
        other_keys.discard("arg_bindings")
        other_keys.discard("for_each_values")
        other_keys.discard("for_each")
        other_keys.discard("for_each_limit")
        for k in other_keys:
            if s1.get(k) != s2.get(k):
                return False

        b1 = s1.get("arg_bindings") or {}
        b2 = s2.get("arg_bindings") or {}
        if b1 != b2:
            return False
        if p in b1:
            return False

        args1 = s1.get("args") or {}
        args2 = s2.get("args") or {}
        if set(args1.keys()) != set(args2.keys()):
            return False
        if p not in args1:
            return False

        for k in args1:
            if k != p:
                if args1[k] != args2[k]:
                    return False
        return True

    def partition_into_collapse_groups(indexed_steps: list[tuple[int, dict]]):
        """Partition steps into groups that can be collapsed together."""
        groups = []
        remaining = list(indexed_steps)
        while remaining:
            i, step = remaining[0]
            args = step.get("args") or {}
            bindings = step.get("arg_bindings") or {}
            if "for_each" in step or "for_each_values" in step:
                remaining.pop(0)
                continue

            found_group = False
            for p in args:
                if p in bindings:
                    continue
                group = []
                for idx, s in remaining:
                    if are_homogeneous_except_p(step, s, p):
                        group.append((idx, s))

                p_values = [s.get("args", {}).get(p) for _, s in group]
                if len(group) >= 2 and len(set(p_values)) == len(group):
                    groups.append((p, group))
                    group_indices = {idx for idx, _ in group}
                    remaining = [item for item in remaining if item[0] not in group_indices]
                    found_group = True
                    break

            if not found_group:
                remaining.pop(0)
        return groups

    all_groups = []
    for op_id, idx_steps in op_to_steps.items():
        if len(idx_steps) >= 2:
            groups = partition_into_collapse_groups(idx_steps)
            if groups:
                all_groups.extend(groups)

    if not all_groups:
        return steps

    import copy
    copied_steps = copy.deepcopy(steps)

    survivor_map = {i: i for i in range(len(copied_steps))}
    removed_indices = set()

    def _cap(lst: list) -> list:
        return lst[:limit] if (isinstance(limit, int) and limit > 0) else lst

    for p, group in all_groups:
        first_idx, first_step = group[0]
        target_step = copied_steps[first_idx]

        vals = [copied_steps[idx].get("args", {}).get(p) for idx, _ in group]
        target_step["for_each_values"] = _cap(vals)
        target_step.setdefault("arg_bindings", {})[p] = "$item"
        target_step.setdefault("args", {}).pop(p, None)
        if isinstance(limit, int) and limit > 0:
            target_step["for_each_limit"] = limit

        for idx, _ in group[1:]:
            survivor_map[idx] = first_idx
            removed_indices.add(idx)

    new_steps = []
    old_to_new = {}
    for old_idx, step in enumerate(copied_steps):
        if old_idx not in removed_indices:
            new_idx = len(new_steps)
            new_steps.append(step)
            old_to_new[old_idx] = new_idx

    for old_idx in range(len(copied_steps)):
        if old_idx in removed_indices:
            survivor_old_idx = survivor_map[old_idx]
            old_to_new[old_idx] = old_to_new[survivor_old_idx]

    _REF = re.compile(r"\$steps\[(\d+)\]")

    def _reindex_refs(value, o2n):
        if isinstance(value, str):
            def repl(m):
                """Regex replacement helper to update step index references."""
                idx = int(m.group(1))
                if idx in o2n:
                    return f"$steps[{o2n[idx]}]"
                return m.group(0)
            return _REF.sub(repl, value)
        elif isinstance(value, dict):
            return {k: _reindex_refs(v, o2n) for k, v in value.items()}
        elif isinstance(value, list):
            return [_reindex_refs(v, o2n) for v in value]
        else:
            return value

    def _reindex_depends_on(dep, o2n):
        if isinstance(dep, int):
            return o2n.get(dep, dep)
        elif isinstance(dep, (list, tuple, set)):
            res = []
            seen = set()
            for x in dep:
                if isinstance(x, int):
                    new_val = o2n.get(x, x)
                    if new_val not in seen:
                        res.append(new_val)
                        seen.add(new_val)
                else:
                    res.append(x)
            if isinstance(dep, tuple):
                return tuple(res)
            if isinstance(dep, set):
                return set(res)
            return res
        return dep

    for step in new_steps:
        if "args" in step:
            step["args"] = _reindex_refs(step["args"], old_to_new)
        if "arg_bindings" in step:
            step["arg_bindings"] = _reindex_refs(step["arg_bindings"], old_to_new)
        if "for_each" in step:
            step["for_each"] = _reindex_refs(step["for_each"], old_to_new)
        if "depends_on" in step:
            step["depends_on"] = _reindex_depends_on(step["depends_on"], old_to_new)

    return new_steps
