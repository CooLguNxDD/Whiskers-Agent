"""Route-candidate adaptation, read/write classification, route resolution, and
token-usage accumulation for the graph state.
Split out of core_graph/node/helpers.py (Phase 4 modularity refactor)."""


def _descriptor_to_candidate(desc) -> dict:
    """Adapt a RouteDescriptor into the candidate dict shape used by the graph."""
    return {
        "operation_id": desc.operation_id,
        "plugin_id": desc.plugin_id,
        "description": desc.description,
        "parameters": desc.parameters,
        "method": desc.method,
        "path_template": desc.path_template,
        "path": desc.path_template,
        "is_fast_path": desc.is_fast_path,
        "score": 0.0,
    }


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def operation_class(route: dict | None) -> str:
    """Classify a resolved route/step as 'read' or 'write' for permission policy.
    Uses the HTTP method (or 'method' in candidate dict). Defaults to 'read'.
    """
    if not route:
        return "read"
    method = str(route.get("method", "") or "").upper()
    if method in _WRITE_METHODS:
        return "write"
    return "read"


def resolve_route_candidate(operation_id, plugin_id, candidates, route_registry):
    """Resolve a step's route candidate: prefer this turn's candidates, else fall
    back to a RouteRegistry lookup (validated by plugin_id). Returns dict or None.
    """
    for c in (candidates or []):
        if c.get("operation_id") == operation_id:
            # Embedder rows may lack parameters (stale index); prefer registry schema.
            if c.get("parameters") is not None:
                return c
            break
    if route_registry is None:
        return None
    try:
        desc = route_registry.get(operation_id, plugin_id)
    except Exception:
        return None
    if desc is None:
        return None
    # Guard: never run a different plugin's tool than the step intended.
    if plugin_id and getattr(desc, "plugin_id", None) != plugin_id:
        return None
    return _descriptor_to_candidate(desc)


def fold_token_usage(state_token_usage: dict | None, new_usage: dict, model_name: str) -> dict:
    """Accumulate token usage for the graph state."""
    if not state_token_usage:
        state_token_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "model_usage": {}
        }
    else:
        state_token_usage.setdefault("input_tokens", 0)
        state_token_usage.setdefault("output_tokens", 0)
        state_token_usage.setdefault("total_tokens", 0)
        state_token_usage.setdefault("model_usage", {})
    
    state_token_usage["input_tokens"] += new_usage.get("input_tokens", 0)
    state_token_usage["output_tokens"] += new_usage.get("output_tokens", 0)
    state_token_usage["total_tokens"] += new_usage.get("total_tokens", 0)
    
    model_stats = state_token_usage.setdefault("model_usage", {}).setdefault(model_name, {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "calls": 0
    })
    
    model_stats["input_tokens"] += new_usage.get("input_tokens", 0)
    model_stats["output_tokens"] += new_usage.get("output_tokens", 0)
    model_stats["total_tokens"] += new_usage.get("total_tokens", 0)
    model_stats["calls"] += 1
    
    return state_token_usage
