"""
Wait injection GOAP utilities.
"""
import copy
import re
from core.plugin_loader.skill_registry import get_all_poll_specs

_REF = re.compile(r"\$steps\[(\d+)\]")

def _shift_refs(value, insert_at):
    if isinstance(value, str):
        def repl(m):
            """Regex replacement helper to shift step index references by one."""
            idx = int(m.group(1))
            if idx >= insert_at:
                return f"$steps[{idx + 1}]"
            return m.group(0)
        return _REF.sub(repl, value)
    elif isinstance(value, dict):
        return {k: _shift_refs(v, insert_at) for k, v in value.items()}
    elif isinstance(value, list):
        return [_shift_refs(v, insert_at) for v in value]
    else:
        return value

def _rewrite_create(value, create_idx):
    if isinstance(value, str):
        return value.replace("$create.", f"$steps[{create_idx}].")
    elif isinstance(value, dict):
        return {k: _rewrite_create(v, create_idx) for k, v in value.items()}
    elif isinstance(value, list):
        return [_rewrite_create(v, create_idx) for v in value]
    else:
        return value

def inject_wait_steps(plan: list[dict], candidates: list[dict]) -> list[dict]:
    """Insert a kind:"wait" poll step after each async-create op that has a
    registered poll spec, when the poll op is available in this turn's candidates.
    Returns a NEW plan list (does not mutate the input steps)."""
    specs = get_all_poll_specs()
    if not specs:
        return plan

    cands_by_op = {}
    for c in candidates:
        op = c.get("operation_id", "")
        if op.startswith("proxy_"):
            op = op[6:]
        cands_by_op[op] = c

    # Create a lookup for spec by "after" op
    # Assuming there's only one spec per 'after' op for simplicity, or we should iterate over specs.
    specs_by_after = {spec["after"]: spec for spec in specs}

    out_plan = copy.deepcopy(plan)
    i = 0
    while i < len(out_plan):
        step = out_plan[i]
        op = step.get("operation_id", "")
        if op.startswith("proxy_"):
            op = op[6:]

        if op in specs_by_after:
            spec = specs_by_after[op]
            poll_op = spec["poll_op"]
            
            if poll_op in cands_by_op:
                cand = cands_by_op[poll_op]
                
                if i + 1 < len(out_plan) and out_plan[i + 1].get("kind") == "wait":
                    next_op = out_plan[i + 1].get("operation_id", "")
                    if next_op.startswith("proxy_"):
                        next_op = next_op[6:]
                    if next_op == poll_op:
                        i += 1
                        continue

                wait_step = {
                    "operation_id": cand.get("operation_id", poll_op),
                    "plugin_id": cand.get("plugin_id", step.get("plugin_id")),
                    "kind": "wait",
                    "intent": f"Poll {poll_op} until {spec['until']}",
                    "args": {},
                    "arg_bindings": {},
                    "match": _rewrite_create(spec.get("match", {}), i),
                    "until": spec["until"],
                    "fail_on": spec.get("fail_on", []),
                    "interval_s": spec.get("interval_s", 10),
                    "max_polls": spec.get("max_polls", 30),
                    "depends_on": i,
                }
                if "is_fast_path" in cand:
                    wait_step["is_fast_path"] = cand["is_fast_path"]

                if "projectId" in step.get("args", {}):
                    wait_step["args"]["projectId"] = step["args"]["projectId"]

                out_plan.insert(i + 1, wait_step)

                for j in range(i + 2, len(out_plan)):
                    out_plan[j] = _shift_refs(out_plan[j], i + 1)
                
                i += 1
        i += 1

    return out_plan
