"""ModelRoleSpec — declarative shape of a pipeline role's model-selection ladder.

A ``ModelRoleSpec`` is the spec+data-driven replacement for hand-coded model
cascades in node Python (e.g. a hardcoded 3-tier triage cascade or a two-pass
GOAP goal-extraction ladder). Plugins/operators declare a role's ladder as
JSON; ``core_graph.model_roles.ladder.run_role_ladder`` is the single executor
every node calls instead of re-implementing escalation logic.

Mirrors ``core_graph.subgraphs.specialist.flow_spec`` deliberately: frozen
dataclasses, strict ``_*_KEYS`` allowlists, fail-closed parsing that never
silently repairs a bad spec. See ``registry.py`` for how specs are stored
(core defaults, DB overrides, plugin contributions) and ``resolver.py`` for
how a ``selector`` string becomes an actual LangChain client.

One deliberate exception to "fail closed": whether a name-type ``selector``
matches an active pool entry is **not** validated here, because the pool is a
dynamic runtime table, not something known at spec-parse time. A typo'd name
is caught later by ``resolver.compile_selector`` (which logs a warning and
falls back to the highest-strength entry) rather than by the parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_ALIASES = frozenset({"core", "strongest", "strong", "balanced", "fast", "weakest"})
_EFFORTS = frozenset({"low", "medium", "high", "max"})  # "effort:<level>" selectors
_TERMINAL_FALLBACKS = frozenset({"ctx_llm", "none", "error"})
_COND_OPS = frozenset({"gt", "gte", "eq", "truthy", "present", "in"})


class ModelRoleSpecError(ValueError):
    """Raised when a model-role spec dict fails validation. Never silently repaired."""


def _require_str(d: dict, key: str, *, where: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ModelRoleSpecError(f"{where}: '{key}' must be a non-empty string")
    return v.strip()


def _opt_str_tuple(d: dict, key: str, *, where: str) -> tuple[str, ...]:
    v = d.get(key, [])
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ModelRoleSpecError(f"{where}: '{key}' must be a list of strings")
    return tuple(v)


@dataclass(frozen=True)
class Rung:
    """One tier of a role's escalation ladder.

    ``selector`` compiles down to a ``resolve_step_llm_config`` hint string:
    an alias (``core``/``fast``/``balanced``/``strongest``/``weakest``), an
    ``effort:<level>`` token, an explicit pool-entry name, or a raw numeric
    strength (e.g. ``"3.5"``).
    """

    selector: str
    max_attempts: int = 1
    timeout_s: float | None = None


@dataclass(frozen=True)
class Validation:
    """Data replacement for an "is this output usable?" Python predicate."""

    require_json: bool = False
    required_keys: tuple[str, ...] = ()
    enum_field: str | None = None
    enum_values: tuple[str, ...] = ()
    non_empty: bool = True

    def check(self, value: Any) -> str | None:
        """Return None when ``value`` passes, else a machine-readable reason string."""
        if self.non_empty and (value is None or value == "" or value == {} or value == []):
            return "invalid_output:empty"
        if self.require_json and not isinstance(value, (dict, list)):
            return "invalid_output:not_json"
        if self.required_keys:
            if not isinstance(value, dict):
                return "invalid_output:not_object"
            missing = [k for k in self.required_keys if k not in value]
            if missing:
                return f"invalid_output:missing_key:{missing[0]}"
        if self.enum_field:
            if not isinstance(value, dict):
                return "invalid_output:not_object"
            got = value.get(self.enum_field)
            if self.enum_values and got not in self.enum_values:
                return f"invalid_output:bad_enum:{self.enum_field}={got!r}"
        return None


@dataclass(frozen=True)
class StateCondition:
    """Pre-emptive rung-start bump, evaluated once against state before rung 0.

    ``op`` is one of: gt, gte, eq, truthy, present, in (state[field] in value).
    """

    field: str
    op: str
    value: Any = None
    advance: int = 1

    def holds(self, state: Any) -> bool:
        """True when this condition matches the given state mapping."""
        if not isinstance(state, dict):
            return False
        present = self.field in state
        v = state.get(self.field)
        if self.op == "present":
            return present and v is not None
        if self.op == "truthy":
            return bool(v)
        if self.op == "eq":
            return v == self.value
        if self.op == "in":
            try:
                return v in self.value
            except TypeError:
                return False
        if self.op in ("gt", "gte"):
            try:
                return (v > self.value) if self.op == "gt" else (v >= self.value)
            except TypeError:
                return False
        return False


@dataclass(frozen=True)
class ModelRoleSpec:
    """One declarative pipeline role: an ordered ladder + escalation rules."""

    role_id: str
    description: str = ""
    ladder: tuple[Rung, ...] = ()
    validate: Validation | None = None
    entry_conditions: tuple[StateCondition, ...] = ()  # pre-emptive start-rung bump
    escalate_on_exception: bool = True  # reactive
    escalate_on_invalid: bool = True
    terminal_fallback: str = "ctx_llm"
    owner: str = ""


def _parse_rung(d: Any, *, index: int, where: str) -> Rung:
    rwhere = f"{where}.ladder[{index}]"
    if not isinstance(d, dict):
        raise ModelRoleSpecError(f"{rwhere}: must be an object")
    unknown = set(d.keys()) - {"selector", "max_attempts", "timeout_s"}
    if unknown:
        raise ModelRoleSpecError(f"{rwhere}: unknown keys {sorted(unknown)}")
    selector = _require_str(d, "selector", where=rwhere)
    max_attempts = d.get("max_attempts", 1)
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ModelRoleSpecError(f"{rwhere}: 'max_attempts' must be a positive int")
    timeout_s = d.get("timeout_s")
    if timeout_s is not None and (not isinstance(timeout_s, (int, float)) or timeout_s <= 0):
        raise ModelRoleSpecError(f"{rwhere}: 'timeout_s' must be a positive number")
    return Rung(
        selector=selector,
        max_attempts=max_attempts,
        timeout_s=float(timeout_s) if timeout_s is not None else None,
    )


_VALIDATION_KEYS = {"require_json", "required_keys", "enum_field", "enum_values", "non_empty"}


def _parse_validation(d: Any, *, where: str) -> Validation | None:
    if d is None:
        return None
    if not isinstance(d, dict):
        raise ModelRoleSpecError(f"{where}: 'validate' must be an object")
    unknown = set(d.keys()) - _VALIDATION_KEYS
    if unknown:
        raise ModelRoleSpecError(f"{where}.validate: unknown keys {sorted(unknown)}")
    require_json = bool(d.get("require_json", False))
    required_keys = _opt_str_tuple(d, "required_keys", where=f"{where}.validate")
    enum_field = d.get("enum_field")
    if enum_field is not None and not isinstance(enum_field, str):
        raise ModelRoleSpecError(f"{where}.validate: 'enum_field' must be a string")
    enum_values = _opt_str_tuple(d, "enum_values", where=f"{where}.validate")
    if enum_values and not enum_field:
        raise ModelRoleSpecError(f"{where}.validate: 'enum_values' requires 'enum_field'")
    non_empty = bool(d.get("non_empty", True))
    return Validation(
        require_json=require_json,
        required_keys=required_keys,
        enum_field=enum_field,
        enum_values=enum_values,
        non_empty=non_empty,
    )


_CONDITION_KEYS = {"field", "op", "value", "advance"}


def _parse_condition(d: Any, *, index: int, where: str) -> StateCondition:
    cwhere = f"{where}.entry_conditions[{index}]"
    if not isinstance(d, dict):
        raise ModelRoleSpecError(f"{cwhere}: must be an object")
    unknown = set(d.keys()) - _CONDITION_KEYS
    if unknown:
        raise ModelRoleSpecError(f"{cwhere}: unknown keys {sorted(unknown)}")
    field_name = _require_str(d, "field", where=cwhere)
    op = d.get("op")
    if op not in _COND_OPS:
        raise ModelRoleSpecError(f"{cwhere}: 'op' must be one of {sorted(_COND_OPS)}")
    advance = d.get("advance", 1)
    if not isinstance(advance, int) or advance < 1:
        raise ModelRoleSpecError(f"{cwhere}: 'advance' must be a positive int")
    return StateCondition(field=field_name, op=op, value=d.get("value"), advance=advance)


_ROLE_KEYS = {
    "role_id", "description", "ladder", "validate", "entry_conditions",
    "escalate_on_exception", "escalate_on_invalid", "terminal_fallback",
}


def parse_model_role_spec(data: dict[str, Any], *, owner: str = "") -> ModelRoleSpec:
    """Parse + strictly validate a model-role spec dict. Raises ``ModelRoleSpecError``.

    Fail-closed by design: unknown top-level/rung/validation/condition keys
    reject rather than being silently ignored. Never repairs input.
    """
    if not isinstance(data, dict):
        raise ModelRoleSpecError("model role spec must be a JSON object")
    unknown = set(data.keys()) - _ROLE_KEYS
    if unknown:
        raise ModelRoleSpecError(f"model role spec: unknown top-level keys {sorted(unknown)}")

    role_id = _require_str(data, "role_id", where="model role spec")
    where = f"model role spec '{role_id}'"

    raw_ladder = data.get("ladder")
    if not isinstance(raw_ladder, list) or not raw_ladder:
        raise ModelRoleSpecError(f"{where}: 'ladder' must be a non-empty list")
    ladder = tuple(_parse_rung(r, index=i, where=where) for i, r in enumerate(raw_ladder))

    validate = _parse_validation(data.get("validate"), where=where)

    raw_conditions = data.get("entry_conditions", [])
    if raw_conditions is None:
        raw_conditions = []
    if not isinstance(raw_conditions, list):
        raise ModelRoleSpecError(f"{where}: 'entry_conditions' must be a list")
    entry_conditions = tuple(
        _parse_condition(c, index=i, where=where) for i, c in enumerate(raw_conditions)
    )

    terminal_fallback = str(data.get("terminal_fallback", "ctx_llm")).strip()
    if terminal_fallback not in _TERMINAL_FALLBACKS:
        raise ModelRoleSpecError(
            f"{where}: 'terminal_fallback' must be one of {sorted(_TERMINAL_FALLBACKS)}"
        )

    return ModelRoleSpec(
        role_id=role_id,
        description=str(data.get("description") or ""),
        ladder=ladder,
        validate=validate,
        entry_conditions=entry_conditions,
        escalate_on_exception=bool(data.get("escalate_on_exception", True)),
        escalate_on_invalid=bool(data.get("escalate_on_invalid", True)),
        terminal_fallback=terminal_fallback,
        owner=owner,
    )


def parse_model_role_bundle(data: dict[str, Any], *, owner: str = "") -> tuple[ModelRoleSpec, ...]:
    """Parse a ``{"roles": [...]}`` bundle (e.g. ``core_roles.json``). Rejects duplicate ids."""
    if not isinstance(data, dict):
        raise ModelRoleSpecError("model role bundle must be a JSON object")
    raw_roles = data.get("roles")
    if not isinstance(raw_roles, list):
        raise ModelRoleSpecError("model role bundle: 'roles' must be a list")
    specs = tuple(parse_model_role_spec(r, owner=owner) for r in raw_roles)
    seen: set[str] = set()
    for s in specs:
        if s.role_id in seen:
            raise ModelRoleSpecError(f"model role bundle: duplicate role_id '{s.role_id}'")
        seen.add(s.role_id)
    return specs
