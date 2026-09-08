"""FlowSpec / StageSpec — declarative shape of a data-driven specialist flow.

A ``FlowSpec`` is the spec+MCP-driven replacement for a hardcoded Python
pipeline (e.g. ``plugins/portfolio_plugin/pipeline.py``'s ``if gclass ==``
ladder). Plugins declare flows as JSON under ``flow_specs/*.json`` and link
them from ``manifest.json``'s ``settings.specialist_agent.flow_specs`` — see
``core.plugin_loader.plugin_registry`` for the loader that turns those files
into registered ``FlowSpec`` instances via ``flow_registry.register_flow``.

Stages reuse the existing primitives rather than inventing new execution
machinery: ``kind="agentic"`` stages run one ``core_graph.agent_loop.run_agent``
call (via ``AgentSpec`` + tool globs), ``kind="deterministic"`` stages dispatch
one catalog operation via ``core.route_registry.execute.execute_operation``.
See ``flow_runner.run_flow`` for the executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_VALID_STAGE_KINDS = frozenset({"agentic", "deterministic"})
_VALID_ON_FAIL = frozenset({"fail_closed", "continue"})
_ON_FAIL_RETRY_PREFIX = "retry:"
# Model-role effort vocabulary (core_graph/model_roles/) — see role_spec.py's
# own _EFFORTS; duplicated here rather than imported to keep flow_spec.py's
# validation self-contained and not depend on the model_roles package.
_VALID_EFFORTS = frozenset({"low", "medium", "high", "max"})


class FlowSpecError(ValueError):
    """Raised when a flow spec dict fails validation. Never silently repaired."""


@dataclass(frozen=True)
class RepeatUntil:
    """Stage retry-loop: re-run ``on_retry_stage`` while ``field`` != target.

    ``field`` is looked up on the stage's own output dict. ``max_rounds`` bounds
    total additional attempts (the first pass always runs once regardless).
    """

    field: str
    equals: Any
    max_rounds: int
    on_retry_stage: str


@dataclass(frozen=True)
class StageSpec:
    """One step of a ``FlowSpec``. Executed by ``flow_runner.run_flow``."""

    id: str
    kind: str  # "agentic" | "deterministic"
    # agentic: prompt content — either a skill body reference or literal text.
    skill: str | None = None
    system_prompt: str | None = None
    # agentic: tool_globs parsed the same way as AgentSpec/ToolRef globs.
    tool_globs: tuple[str, ...] = ()
    # deterministic: a single "plugin_id/operation_id" catalog op to dispatch.
    op: str | None = None
    output_schema: dict[str, Any] | None = None
    required_outputs: tuple[str, ...] = ()
    # Blackboard slot names this stage reads from / writes to.
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    max_steps: int = 12
    max_seconds: float = 90.0
    on_fail: str = "fail_closed"  # "fail_closed" | "continue" | "retry:N"
    repeat_until: RepeatUntil | None = None
    # Model selection for agentic stages only (§8.2 of the model-role-specs
    # design) — one of `model` (explicit selector, "role:<id>", or a raw
    # numeric strength) or `effort` (low|medium|high|max, mapped to a
    # selector via the model-role layer's effort_map). Mutually exclusive;
    # both are rejected on deterministic stages (nothing to select a model
    # for). See flow_runner._run_agentic_stage for the resolution chain.
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True)
class ClaimRule:
    """Data replacement for a Python ``*_domain_claims(goal)`` predicate."""

    goal_classes: tuple[str, ...] = ()
    any_keywords: tuple[str, ...] = ()
    deny_keywords: tuple[str, ...] = ()

    def matches(self, *, goal: str, goal_class: str | None) -> bool:
        """True when this rule claims the given goal/goal_class."""
        q = (goal or "").lower()
        if any(k in q for k in self.deny_keywords):
            return False
        if self.goal_classes and goal_class in self.goal_classes:
            return True
        if self.any_keywords and any(k in q for k in self.any_keywords):
            return True
        return False


@dataclass(frozen=True)
class FlowSpec:
    """One declarative specialist flow: claim rule + ordered stages."""

    flow_id: str
    name: str
    claims: ClaimRule
    stages: tuple[StageSpec, ...]
    requires: tuple[str, ...] = ()  # GOAP fact preconditions (e.g. "have:job_signals")
    provides: tuple[str, ...] = ()  # GOAP fact effects (e.g. "did:bake_portfolio")
    inputs_schema: dict[str, Any] | None = None
    owner: str = ""  # plugin_id, stamped by the registry at register time
    # Flow-wide default effort for agentic stages that don't declare their own
    # model/effort (§8.5 resolution chain: stage.model > stage.effort >
    # flow.effort > manifest effort_overrides[goal_class] > manifest effort >
    # subgraph.model_role > core role "specialist" > core LLM).
    effort: str | None = None


def _require_str(d: dict, key: str, *, where: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise FlowSpecError(f"{where}: '{key}' must be a non-empty string")
    return v.strip()


def _opt_str_tuple(d: dict, key: str, *, where: str) -> tuple[str, ...]:
    v = d.get(key, [])
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise FlowSpecError(f"{where}: '{key}' must be a list of strings")
    return tuple(v)


def _parse_repeat_until(d: Any, *, stage_ids: set[str], where: str) -> RepeatUntil | None:
    if d is None:
        return None
    if not isinstance(d, dict):
        raise FlowSpecError(f"{where}: 'repeat_until' must be an object")
    unknown = set(d.keys()) - {"field", "equals", "max_rounds", "on_retry_stage"}
    if unknown:
        raise FlowSpecError(f"{where}: repeat_until has unknown keys {sorted(unknown)}")
    field_name = _require_str(d, "field", where=f"{where}.repeat_until")
    if "equals" not in d:
        raise FlowSpecError(f"{where}.repeat_until: 'equals' is required")
    max_rounds = d.get("max_rounds", 1)
    if not isinstance(max_rounds, int) or max_rounds < 1:
        raise FlowSpecError(f"{where}.repeat_until: 'max_rounds' must be a positive int")
    target = _require_str(d, "on_retry_stage", where=f"{where}.repeat_until")
    if target not in stage_ids:
        raise FlowSpecError(
            f"{where}.repeat_until: on_retry_stage '{target}' is not a declared stage id"
        )
    return RepeatUntil(field=field_name, equals=d["equals"], max_rounds=max_rounds, on_retry_stage=target)


def _parse_on_fail(v: Any, *, where: str) -> str:
    s = str(v or "fail_closed").strip()
    if s in _VALID_ON_FAIL:
        return s
    if s.startswith(_ON_FAIL_RETRY_PREFIX):
        n = s[len(_ON_FAIL_RETRY_PREFIX):]
        if n.isdigit() and int(n) >= 1:
            return s
    raise FlowSpecError(
        f"{where}: 'on_fail' must be 'fail_closed', 'continue', or 'retry:N' (got {s!r})"
    )


_STAGE_KEYS = {
    "id", "kind", "skill", "system_prompt", "tool_globs", "op", "output_schema",
    "required_outputs", "reads", "writes", "max_steps", "max_seconds", "on_fail",
    "repeat_until", "model", "effort",
}


def _parse_stage(d: dict, *, index: int, all_stage_ids: set[str]) -> StageSpec:
    where = f"stages[{index}]"
    if not isinstance(d, dict):
        raise FlowSpecError(f"{where}: must be an object")
    unknown = set(d.keys()) - _STAGE_KEYS
    if unknown:
        raise FlowSpecError(f"{where}: unknown keys {sorted(unknown)}")

    sid = _require_str(d, "id", where=where)
    kind = _require_str(d, "kind", where=where)
    if kind not in _VALID_STAGE_KINDS:
        raise FlowSpecError(f"{where}: 'kind' must be one of {sorted(_VALID_STAGE_KINDS)}")

    op = d.get("op")
    if op is not None and not isinstance(op, str):
        raise FlowSpecError(f"{where}: 'op' must be a string")
    if kind == "deterministic" and not op:
        raise FlowSpecError(f"{where}: deterministic stages require 'op'")

    output_schema = d.get("output_schema")
    if output_schema is not None and not isinstance(output_schema, dict):
        raise FlowSpecError(f"{where}: 'output_schema' must be an object")

    max_steps = d.get("max_steps", 12)
    if not isinstance(max_steps, int) or max_steps < 1:
        raise FlowSpecError(f"{where}: 'max_steps' must be a positive int")
    max_seconds = d.get("max_seconds", 90.0)
    if not isinstance(max_seconds, (int, float)) or max_seconds <= 0:
        raise FlowSpecError(f"{where}: 'max_seconds' must be a positive number")

    model = d.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise FlowSpecError(f"{where}: 'model' must be a non-empty string")
    effort = d.get("effort")
    if effort is not None and effort not in _VALID_EFFORTS:
        raise FlowSpecError(f"{where}: 'effort' must be one of {sorted(_VALID_EFFORTS)}")
    if model is not None and effort is not None:
        raise FlowSpecError(f"{where}: 'model' and 'effort' are mutually exclusive")
    if kind == "deterministic" and (model is not None or effort is not None):
        raise FlowSpecError(f"{where}: deterministic stages cannot declare 'model'/'effort' (nothing to select a model for)")

    return StageSpec(
        id=sid,
        kind=kind,
        skill=d.get("skill"),
        system_prompt=d.get("system_prompt"),
        tool_globs=_opt_str_tuple(d, "tool_globs", where=where),
        op=op,
        output_schema=output_schema,
        required_outputs=_opt_str_tuple(d, "required_outputs", where=where),
        reads=_opt_str_tuple(d, "reads", where=where),
        writes=_opt_str_tuple(d, "writes", where=where),
        max_steps=max_steps,
        max_seconds=float(max_seconds),
        on_fail=_parse_on_fail(d.get("on_fail"), where=where),
        repeat_until=_parse_repeat_until(d.get("repeat_until"), stage_ids=all_stage_ids, where=where),
        model=model.strip() if isinstance(model, str) else None,
        effort=effort,
    )


def _parse_claims(d: Any, *, where: str) -> ClaimRule:
    if d is None:
        return ClaimRule()
    if not isinstance(d, dict):
        raise FlowSpecError(f"{where}: 'claims' must be an object")
    unknown = set(d.keys()) - {"goal_classes", "any_keywords", "deny_keywords"}
    if unknown:
        raise FlowSpecError(f"{where}.claims: unknown keys {sorted(unknown)}")
    return ClaimRule(
        goal_classes=_opt_str_tuple(d, "goal_classes", where=f"{where}.claims"),
        any_keywords=tuple(k.lower() for k in _opt_str_tuple(d, "any_keywords", where=f"{where}.claims")),
        deny_keywords=tuple(k.lower() for k in _opt_str_tuple(d, "deny_keywords", where=f"{where}.claims")),
    )


_FLOW_KEYS = {
    "flow_id", "name", "claims", "stages", "requires", "provides", "inputs_schema", "effort",
}


def parse_flow_spec(data: dict[str, Any], *, owner: str = "") -> FlowSpec:
    """Parse + strictly validate a flow spec dict. Raises ``FlowSpecError``.

    Fail-closed by design: unknown top-level or per-stage keys reject rather
    than being silently ignored (a typo in a plugin's JSON must not produce a
    flow that quietly skips a stage). Never repairs input.
    """
    if not isinstance(data, dict):
        raise FlowSpecError("flow spec must be a JSON object")
    unknown = set(data.keys()) - _FLOW_KEYS
    if unknown:
        raise FlowSpecError(f"flow spec: unknown top-level keys {sorted(unknown)}")

    flow_id = _require_str(data, "flow_id", where="flow spec")
    name = _require_str(data, "name", where="flow spec")

    raw_stages = data.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise FlowSpecError(f"flow spec '{flow_id}': 'stages' must be a non-empty list")

    stage_ids: list[str] = []
    for s in raw_stages:
        if isinstance(s, dict) and isinstance(s.get("id"), str):
            stage_ids.append(s["id"])
    if len(stage_ids) != len(set(stage_ids)):
        raise FlowSpecError(f"flow spec '{flow_id}': duplicate stage ids")
    stage_id_set = set(stage_ids)

    stages = tuple(
        _parse_stage(s, index=i, all_stage_ids=stage_id_set) for i, s in enumerate(raw_stages)
    )

    claims = _parse_claims(data.get("claims"), where=f"flow spec '{flow_id}'")
    inputs_schema = data.get("inputs_schema")
    if inputs_schema is not None and not isinstance(inputs_schema, dict):
        raise FlowSpecError(f"flow spec '{flow_id}': 'inputs_schema' must be an object")

    flow_effort = data.get("effort")
    if flow_effort is not None and flow_effort not in _VALID_EFFORTS:
        raise FlowSpecError(f"flow spec '{flow_id}': 'effort' must be one of {sorted(_VALID_EFFORTS)}")

    return FlowSpec(
        flow_id=flow_id,
        name=name,
        claims=claims,
        stages=stages,
        requires=_opt_str_tuple(data, "requires", where=f"flow spec '{flow_id}'"),
        provides=_opt_str_tuple(data, "provides", where=f"flow spec '{flow_id}'"),
        inputs_schema=inputs_schema,
        owner=owner,
        effort=flow_effort,
    )
