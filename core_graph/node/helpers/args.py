"""Argument resolution: $steps[] binding resolution, working-memory fallback,
parameter coercion/normalization, and context-params construction.
Split out of core_graph/node/helpers.py (Phase 4 modularity refactor)."""
import logging
import re
from typing import Any

from core_graph.node.helpers._shared import _RESULT_ENVELOPE_KEYS, _BINDING_RE, _ID_ALIASES, _MAX_DEEP_FIND_DEPTH
from utils.server_config import PARAMETERS as _SERVER_PARAMETERS

logger = logging.getLogger("whiskers")


def _walk_path(value: Any, path: str) -> Any:
    """Follow a dotted path through nested dicts and lists, parsing list indices [n]."""
    if not path:
        return value
    segments = [p for p in path.split(".") if p]
    for segment in segments:
        brackets = re.findall(r"\[(\d+)\]", segment)
        base_name = re.sub(r"\[\d+\]", "", segment)
        
        if base_name:
            if isinstance(value, dict):
                value = value.get(base_name)
            else:
                return None
                
        for idx_str in brackets:
            idx = int(idx_str)
            if isinstance(value, list) and 0 <= idx < len(value):
                value = value[idx]
            elif isinstance(value, dict) and idx_str in value:
                value = value.get(idx_str)
            else:
                return None
    return value


def _deep_find_key(obj: Any, key: str, _depth: int = 0) -> Any:
    """Depth-first search for the first non-None value under *key* anywhere.
    If key is id-like, accepts any key in _ID_ALIASES (case-insensitive and alias-aware)."""
    if _depth > _MAX_DEEP_FIND_DEPTH:
        return None
    target_key_lower = key.lower()
    is_id_like = target_key_lower in _ID_ALIASES
    
    if isinstance(obj, dict):
        if is_id_like:
            for k in obj:
                if k.lower() in _ID_ALIASES and obj[k] is not None:
                    return obj[k]
        else:
            if obj.get(key) is not None:
                return obj[key]
                
        for v in obj.values():
            found = _deep_find_key(v, key, _depth=_depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _deep_find_key(item, key, _depth=_depth + 1)
            if found is not None:
                return found
    return None


def harvest_all_ids_from_envelope(obj: Any, resource_hint: str = "") -> list[str]:
    """Collect ALL distinct id-like string values from search/list result envelopes.

    Returns a flat list of unique id strings from list envelopes (results, items,
    data.results, data). Preserves insertion order. Used by fold_working_memory to
    build full *_ids lists so fan-out planning can reference all items, not just the first.
    """
    ids: list[str] = []
    seen: set[str] = set()

    def _add(v: Any) -> None:
        s = str(v) if isinstance(v, (str, int)) and v else None
        if s and s not in seen:
            seen.add(s)
            ids.append(s)

    def _scan_item(item: Any) -> None:
        if not isinstance(item, dict):
            return
        for k, v in item.items():
            k_lower = k.lower()
            if k_lower in _ID_ALIASES or k_lower.endswith("_id") or k_lower == "page_id":
                _add(v)

    seen_list_ids: list[int] = []
    candidate_lists: list[list] = []

    def _try_add(lst: Any) -> None:
        if isinstance(lst, list) and id(lst) not in seen_list_ids:
            seen_list_ids.append(id(lst))
            candidate_lists.append(lst)

    if isinstance(obj, dict):
        for env in ("results", "items"):
            _try_add(obj.get(env))
        data = obj.get("data")
        if isinstance(data, list):
            _try_add(data)
        elif isinstance(data, dict):
            for env in ("results", "items"):
                _try_add(data.get(env))
        for env in _RESULT_ENVELOPE_KEYS:
            _try_add(obj.get(env))
    elif isinstance(obj, list):
        _try_add(obj)

    for lst in candidate_lists:
        for item in lst:
            _scan_item(item)

    return ids


def _resolve_step_value(root: Any, path: str) -> Any:
    """Resolve a dotted path against a step result, tolerant of result
    envelopes and wrong path guesses (returns None if unresolvable)."""
    # 1. Try the literal/index-aware path directly against the current root
    value = _walk_path(root, path)
    if value is not None:
        return value
    if not path:
        return root or None

    # 2. Try peeling wrapper keys recursively
    if isinstance(root, dict):
        for env in _RESULT_ENVELOPE_KEYS:
            inner = root.get(env)
            if inner is not None:
                # Try full path on this peeled layer
                val = _resolve_step_value(inner, path)
                if val is not None:
                    return val

    # 3. Last resort: find the final path segment as a key name anywhere.
    base_final = re.sub(r"\[\d+\]", "", path.split(".")[-1])
    return _deep_find_key(root, base_final)


def _resolve_arg_bindings(
    bindings: dict,
    step_results: list[dict],
) -> dict:
    """Resolve `$steps[<idx>].<dotted.path>` references against prior outputs.

    Tolerant of (a) result envelopes like ``{"status":"ok","data":{...}}`` that
    ``validator_node`` wraps around each step's output, and (b) the planner
    guessing the wrong field path. Non-reference values pass through untouched.
    """
    resolved: dict[str, Any] = {}
    for key, ref in (bindings or {}).items():
        if not isinstance(ref, str):
            resolved[key] = ref
            continue
        m = _BINDING_RE.match(ref.strip())
        if not m:
            resolved[key] = ref  # not a $steps reference — pass literal through
            continue
        idx = int(m.group(1))
        if idx >= len(step_results):
            continue
        value = _resolve_step_value(step_results[idx], m.group(2) or "")
        if value is not None:
            resolved[key] = value
    return resolved


def resolve_args_from_context(
    step: dict,
    step_results: list[dict],
    known_params: dict,
    is_fast_path: bool = False,
    fn: Any = None,
    route: dict = None,
    working_memory: dict | None = None,
) -> tuple[dict, list[str]]:
    """Harden argument resolution for a step across prior step_results and known_params.
    Returns (resolved_args, unresolved_required).
    """
    import inspect
    # 1. Start with step.args
    resolved = dict(step.get("args") or {})
    
    # 2. Resolve arg_bindings
    bindings = step.get("arg_bindings") or {}
    resolved_bindings = _resolve_arg_bindings(bindings, step_results)
    for k, v in resolved_bindings.items():
        if v is not None:
            resolved[k] = v

    # 3. Determine all expected and required parameters
    all_params = []
    required_params = []
    
    route_parameters = route.get("parameters", {}) if route else {}
    if isinstance(route_parameters, dict):
        if "properties" in route_parameters:
            all_params = list(route_parameters.get("properties", {}).keys())
            required_params = list(route_parameters.get("required", []))
        else:
            all_params = list(route_parameters.keys())
            required_params = list(route_parameters.keys())

    # Also extract path/query parameters from route's path_template or path
    if route:
        path = route.get("path") or route.get("path_template", "")
        # Extract {param}
        for p in re.findall(r"\{([^}]+)\}", path):
            if p not in required_params:
                required_params.append(p)
            if p not in all_params:
                all_params.append(p)
        # Extract :param
        for p in re.findall(r":([a-zA-Z0-9_]+)", path):
            if p not in required_params:
                required_params.append(p)
            if p not in all_params:
                all_params.append(p)

    # Analyze function signature if available (for fast-path)
    if is_fast_path and fn is not None:
        try:
            sig = inspect.signature(fn)
            for name, param in sig.parameters.items():
                if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                    if name not in all_params:
                        all_params.append(name)
                    if param.default is inspect.Parameter.empty:
                        if name not in required_params:
                            required_params.append(name)
        except Exception as exc:
            logger.debug("fast-path signature inspect failed: %s", exc)

    # 4. Merge/inject defaults from known_params only for expected parameters.
    for p_name in all_params:
        if resolved.get(p_name) is None or resolved.get(p_name) == "":
            val = known_params.get(p_name)
            if val is None:
                # Try all conversions to find defaults
                snake = re.sub(r'(?<!^)(?=[A-Z])', '_', p_name).lower()
                camel = _to_camel(p_name) if "_" in p_name else p_name
                u_snake = snake.upper()
                u_camel = camel.upper()
                
                variations = [
                    snake,
                    camel,
                    u_snake,
                    u_camel,
                    f"WHISKERS_{u_snake}",
                    f"WHISKERS_{u_camel}"
                ]
                for var in variations:
                    val = known_params.get(var)
                    if val is not None:
                        break
            if val is not None:
                resolved[p_name] = _coerce(val)

    # 5. Forgiving deep-scan of all prior step_results for still-missing required params
    unresolved_required = []
    for p_name in required_params:
        if resolved.get(p_name) is None or resolved.get(p_name) == "":
            found_val = None
            search_keys = [p_name]
            p_name_lower = p_name.lower()
            if p_name_lower in _ID_ALIASES:
                search_keys.extend(list(_ID_ALIASES))
                
            for res in reversed(step_results):
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
                unresolved_required.append(p_name)

    if working_memory and unresolved_required:
        resolved, unresolved_required = fill_missing_from_memory(
            unresolved_required, resolved, working_memory
        )

    return resolved, unresolved_required


def _build_context_params(
    overrides: dict | None = None,
    parameters: dict | None = None,
) -> dict[str, Any]:
    """
    Derive a flat {snake_case: val, camelCase: val} dict from server_config PARAMETERS.
    Keys like DEFAULT_PROJECT_ID (or legacy WHISKERS_PROJECT_ID) become both
    'project_id' and 'projectId'.
    `overrides` values take precedence over PARAMETERS values.
    """
    result: dict[str, Any] = {}
    for env_key, val in (parameters or _SERVER_PARAMETERS).items():
        bare = env_key
        for prefix in ("WHISKERS_", "PLUGIN_", "DEFAULT_"):
            if bare.startswith(prefix):
                bare = bare[len(prefix):]
                break
        bare = bare.lower()
        parts = bare.split("_")
        camel = parts[0] + "".join(p.capitalize() for p in parts[1:])  # "projectId"
        result[bare] = val
        result[camel] = val
    if overrides:
        result.update(overrides)
    return result


def _coerce(val: Any) -> Any:
    try:
        return int(val)
    except (TypeError, ValueError):
        return val


def _to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def fill_missing_from_memory(missing: list[str], args: dict, working_memory: dict | None) -> tuple[dict, list]:
    """Attempts to fill missing parameters defensively from working memory using exact or derived keys."""
    args2 = dict(args)
    still = []
    mem = working_memory or {}
    for p in missing:
        val = None
        if p in mem:
            val = mem[p]
        else:
            p_lower = p.lower()
            if p_lower in _ID_ALIASES:
                # Try case-insensitive lookup in root keys first if it matches _ID_ALIASES
                for k, v in mem.items():
                    if k.lower() in _ID_ALIASES and v is not None:
                        val = v
                        break
                if val is None:
                    val = _deep_find_key(mem, p)
            else:
                # otherwise try camel/snake variants in working_memory
                snake = re.sub(r"(?<!^)(?=[A-Z])", "_", p).lower()
                camel = _to_camel(p) if "_" in p else p
                if snake in mem:
                    val = mem[snake]
                elif camel in mem:
                    val = mem[camel]
                # check case-insensitive direct match
                if val is None:
                    for k, v in mem.items():
                        if k.lower() == p_lower:
                            val = v
                            break
                # fallback to deep search
                if val is None:
                    val = _deep_find_key(mem, p)
        if val is not None:
            args2[p] = _coerce(val)
        else:
            still.append(p)
    return args2, still


def _normalize_arg_keys(args: dict, sig_params: dict) -> dict:
    """Remap camelCase arg keys to snake_case when the snake_case form exists in sig_params.

    Planners emit camelCase names (matching upstream API conventions); Python tool
    signatures use snake_case. Without this normalization the pruning step at
    each fast-path call site drops camelCase keys entirely, leaving required
    params unset and triggering missing-field validation errors.
    """
    result = {}
    for k, v in args.items():
        if k not in sig_params:
            snake = re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower()
            result[snake if snake in sig_params else k] = v
        else:
            result[k] = v
    return result
