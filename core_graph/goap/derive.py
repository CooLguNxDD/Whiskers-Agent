"""
GOAP Action derivation utilities from route candidates.
"""

import re
from core_graph.goap.action import GoapAction


def _path_params(candidate: dict) -> list[str]:
    """
    Extract path and template parameters from candidate's path/path_template.
    """
    path = candidate.get("path") or candidate.get("path_template") or ""
    params = []
    # Extract {param}
    for p in re.findall(r"\{([^}]+)\}", path):
        if p not in params:
            params.append(p)
    # Extract :param
    for p in re.findall(r":([a-zA-Z0-9_]+)", path):
        if p not in params:
            params.append(p)
    return params


def _required_params(candidate: dict) -> list[str]:
    """
    Determine all required parameters for a candidate, unioned with path params.
    """
    required = []
    route_parameters = candidate.get("parameters", {})
    if isinstance(route_parameters, dict):
        if "properties" in route_parameters:
            required = list(route_parameters.get("required", []))
        else:
            required = [
                n for n, sch in route_parameters.items()
                if isinstance(sch, dict) and sch.get("required")
            ]
    # Union with path/template params:
    path_p = _path_params(candidate)
    seen = set(required)
    for p in path_p:
        if p not in seen:
            required.append(p)
            seen.add(p)
    return required


def _plugin_prefixes(plugin_id: str) -> list[str]:
    """Candidate name-prefixes a plugin's tools might glue onto their verb
    (e.g. "jules_plugin" -> ["jules_plugin", "julesplugin", "jules"]),
    longest first so the most specific strip is tried first."""
    if not plugin_id:
        return []
    pid = plugin_id.lower()
    prefixes = {pid, pid.replace("_", "").replace("-", "")}
    for suffix in ("_plugin", "-plugin", "plugin", "_mcp", "-mcp"):
        if pid.endswith(suffix) and len(pid) > len(suffix):
            prefixes.add(pid[: -len(suffix)].rstrip("_-"))
    return sorted((p for p in prefixes if len(p) >= 3), key=len, reverse=True)


def _strip_plugin_prefix(op_id: str, plugin_id: str) -> str:
    """Strip a plugin-derived prefix from op_id if present (e.g. tool_prefix
    naming conventions that glue the plugin name directly onto the verb with
    no delimiter, like "julesget_session"). Returns op_id unchanged if no
    plugin-derived prefix matches."""
    low = op_id.lower()
    for pref in _plugin_prefixes(plugin_id):
        if low.startswith(pref) and len(op_id) > len(pref):
            rest = op_id[len(pref):].lstrip("_-")
            if rest:
                return rest
    return op_id


def _proxy_stripped(operation_id: str) -> str:
    return operation_id.rpartition("__")[-1] if "__" in operation_id else operation_id


def _tokens_of(op_id: str) -> list[str]:
    """camelCase/snake-case tokenization used by the collection/consumer verb checks."""
    op_id_sep = re.sub(r"(?<!^)(?=[A-Z])", "_", op_id)
    return re.split(r"[^a-zA-Z0-9]+", op_id_sep.lower())


_RESOURCE_VERBS = {
    "create", "get", "list", "update", "delete", "add", "send",
    "fetch", "find", "search", "post", "put", "patch"
}


def _extract_noun_by_prefix(op_id: str, verbs: set[str]) -> str:
    """Anchored prefix-verb extraction (verb must be a clean prefix followed
    by '_' or an uppercase boundary). Mirrors the original single-pass logic
    so behavior for already-resolving op ids is unchanged byte-for-byte."""
    for verb in sorted(verbs, key=len, reverse=True):
        if op_id.lower().startswith(verb):
            verb_len = len(verb)
            if len(op_id) == verb_len:
                return ""
            next_char = op_id[verb_len]
            if next_char == "_" or next_char.isupper():
                return op_id[verb_len:].lstrip("_")
            # Prefix matched but no clean boundary — keep scanning other verbs,
            # same as the original loop (no break here).
    return ""


def _resource_of(candidate: dict) -> str:
    """
    Derive the resource noun from the candidate route's operation_id or path.
    """
    op_id = candidate.get("operation_id") or ""
    if "__" in op_id:
        op_id = op_id.rpartition("__")[-1]
    verbs = _RESOURCE_VERBS

    # Pass 1: verb as a clean prefix of the operation_id (original behavior).
    noun = _extract_noun_by_prefix(op_id, verbs)

    # Pass 2: verb glued directly onto a plugin-name prefix with no delimiter
    # (e.g. "julesget_session") — strip the plugin-derived prefix and retry.
    if not noun:
        plugin_id = candidate.get("plugin_id", "")
        stripped_op = _strip_plugin_prefix(op_id, plugin_id)
        if stripped_op != op_id:
            noun = _extract_noun_by_prefix(stripped_op, verbs)

    # If we have a noun, lowercase and naive-singularize it
    if noun:
        noun_lower = noun.lower()
        if noun_lower.endswith("s"):
            return noun_lower[:-1]
        return noun_lower

    # Fallback: last static segment of the path, singularized
    path = candidate.get("path") or candidate.get("path_template") or ""
    segments = [seg for seg in path.split("/") if seg]
    static_segments = []
    for seg in segments:
        if (seg.startswith("{") and seg.endswith("}")) or seg.startswith(":"):
            continue
        static_segments.append(seg)

    if static_segments:
        last_seg = static_segments[-1].lower()
        if last_seg.endswith("s"):
            return last_seg[:-1]
        return last_seg

    return ""


_CONSUMER_VERBS = {"fetch", "get", "retrieve", "read", "show", "open"}


def _schema_param_names(candidate: dict) -> set[str]:
    """All param names in a candidate (JSON-schema properties or flat keys)."""
    p = candidate.get("parameters", {})
    if not isinstance(p, dict):
        return set()
    if "properties" in p and isinstance(p["properties"], dict):
        return set(p["properties"].keys())
    return {k for k, v in p.items() if isinstance(v, dict)}


def _id_consume_precondition(candidate: dict) -> str | None:
    """For a non-collection by-id consumer op with a literal `id` param,
    return 'have:id' so GOAP binds it from a prior collection op."""
    op_id = candidate.get("operation_id", "")
    plugin_id = candidate.get("plugin_id", "")
    if _is_collection_op(op_id, plugin_id):
        return None
    stripped = _proxy_stripped(op_id)
    tokens = _tokens_of(stripped)
    matched = any(t in _CONSUMER_VERBS for t in tokens)
    if not matched and plugin_id:
        prefix_stripped = _strip_plugin_prefix(stripped, plugin_id)
        if prefix_stripped != stripped:
            matched = any(t in _CONSUMER_VERBS for t in _tokens_of(prefix_stripped))
    if not matched:
        return None
    if "id" in _schema_param_names(candidate):
        return "have:id"
    return None


_ID_ALIASES = {"id", "userid", "user_id", "_id"}
_COLLECTION_VERBS = {"search", "list", "find", "query"}


def _is_collection_op(operation_id: str, plugin_id: str = "") -> bool:
    """
    Check if the proxy-stripped operation ID represents a collection operation.
    Falls back to a plugin-prefix-stripped retry for names that glue the verb
    directly onto the plugin name with no delimiter (e.g. "juleslist_sessions").
    """
    stripped_op = _proxy_stripped(operation_id)
    if any(t in _COLLECTION_VERBS for t in _tokens_of(stripped_op)):
        return True
    if plugin_id:
        prefix_stripped = _strip_plugin_prefix(stripped_op, plugin_id)
        if prefix_stripped != stripped_op and any(t in _COLLECTION_VERBS for t in _tokens_of(prefix_stripped)):
            return True
    return False


def derive_action(candidate: dict) -> GoapAction:
    """
    Derive a GoapAction from a RouteCandidate dictionary.
    """
    operation_id = candidate.get("operation_id", "")
    plugin_id = candidate.get("plugin_id", "")
    intent = candidate.get("description", "")

    # Preconditions
    req_params = _required_params(candidate)
    path_p_set = set(_path_params(candidate))
    is_get = candidate.get("method", "GET").upper() == "GET"
    preconditions = set()
    precondition_params_list = []
    for p in req_params:
        if p.lower() in _ID_ALIASES and not (is_get and p in path_p_set):
            preconditions.add("have:id")
            precondition_params_list.append(("have:id", p))
        else:
            preconditions.add(f"have:{p}")

    consume = _id_consume_precondition(candidate)
    if consume:
        preconditions.add(consume)
        if not any(fact == "have:id" for fact, _ in precondition_params_list):
            precondition_params_list.append(("have:id", "id"))

    preconditions = frozenset(preconditions)
    precondition_params = tuple(sorted(list(set(precondition_params_list))))

    # Effects
    effects_set = {f"did:{operation_id}"}
    effect_fields_dict = {}

    resource = _resource_of(candidate)
    if resource:
        method = candidate.get("method", "GET").upper()
        if method in {"POST", "PUT", "PATCH"}:
            effects_set.add(f"have:{resource}_id")
            effect_fields_dict[f"have:{resource}_id"] = "id"
            effects_set.add("have:id")
            effect_fields_dict["have:id"] = "id"
            effects_set.add(f"have:{resource}")
            effect_fields_dict[f"have:{resource}"] = resource
        else:
            effects_set.add(f"have:{resource}")
            effect_fields_dict[f"have:{resource}"] = resource

    # Apply default rule for any 'have:<x>' effect not otherwise mapped
    for eff in effects_set:
        if eff.startswith("have:") and eff not in effect_fields_dict:
            field_name = eff[len("have:"):]
            effect_fields_dict[eff] = field_name

    # Add id-producing effects for collection operations
    if _is_collection_op(operation_id, plugin_id):
        effects_set.add("have:id")
        effect_fields_dict["have:id"] = "id"
        if resource:
            effects_set.add(f"have:{resource}_id")
            effect_fields_dict[f"have:{resource}_id"] = "id"
            # Also bridge the naive lowerCamelCase convention some tool
            # schemas use for their by-id param (e.g. Jules' "sessionId" for
            # resource "session") — this satisfies a consumer op's plain,
            # unmodified `have:{resource}Id` precondition (the default for
            # any required param not in _ID_ALIASES) without needing a
            # broader/riskier generalization on the precondition side, which
            # would collide with params like "projectId" that are
            # intentionally satisfied verbatim from seeded context defaults.
            effects_set.add(f"have:{resource}Id")
            effect_fields_dict[f"have:{resource}Id"] = "id"

    # Convert effect_fields_dict to sorted tuple of tuples for deterministic ordering/hashing
    effect_fields = tuple(sorted(effect_fields_dict.items()))

    model = candidate.get("model") or (candidate.get("metadata") or {}).get("model")

    return GoapAction(
        operation_id=operation_id,
        plugin_id=plugin_id,
        intent=intent,
        preconditions=preconditions,
        effects=frozenset(effects_set),
        effect_fields=effect_fields,
        cost=1.0,
        candidate=candidate,
        model=model,
        precondition_params=precondition_params,
    )


def derive_actions(candidates: list[dict]) -> list[GoapAction]:
    """
    Derive a list of GoapActions from a list of RouteCandidate dictionaries.
    """
    return [derive_action(c) for c in candidates]
