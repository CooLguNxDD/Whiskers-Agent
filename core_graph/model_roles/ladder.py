"""The single executor for a ModelRoleSpec — nodes call this instead of hand-coding cascades.

``run_role_ladder`` walks a role's rungs, resolving each through
``core_graph.model_roles.resolver.resolve_role_llm``, invoking the caller's
``attempt`` callable, extracting/validating the result, and escalating on
failure per the spec. Two escalation kinds are handled, deliberately kept
separate (see ``role_spec.py``):

- ``entry_conditions`` — pre-emptive, evaluated once against ``state`` before
  the first attempt (e.g. "start one rung higher when this is a replan").
- ``escalate_on_exception`` / ``escalate_on_invalid`` — reactive, evaluated
  per attempt outcome (e.g. "the small model's JSON didn't parse, try the
  next rung").

Backward-compat guarantee: when the spec is absent (unregistered role, empty
registry, DB down) or the ``MODEL_ROLES_ENABLED`` flag is off, the ladder
degrades to a single attempt against ``fallback_llm`` — today's behaviour,
with audit + token accounting recorded regardless.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from core_graph.model_roles.role_spec import ModelRoleSpec, Rung, Validation

logger = logging.getLogger("whiskers.core_graph.model_roles.ladder")

_FALLBACK_SPEC = ModelRoleSpec(role_id="__fallback__", ladder=(Rung(selector="core"),))


@dataclass
class RungAttempt:
    """Record of one (rung, attempt-within-rung) call."""

    rung_index: int
    selector: str
    model: str
    status: str  # "ok" | "invalid" | "error"
    reason: str | None = None
    elapsed_ms: int = 0


@dataclass
class LadderResult:
    """Outcome of a full ``run_role_ladder`` call."""

    value: Any = None
    llm: Any | None = None
    rung_index: int = 0
    escalated: bool = False
    attempts: list[RungAttempt] = field(default_factory=list)
    token_usage: dict | None = None
    status: str = "ok"  # "ok" | "exhausted"
    role: str | None = None

    def audit_entry(self, node: str) -> dict:
        """Compact per-node record for ``state['model_audit']``."""
        last = self.attempts[-1] if self.attempts else None
        return {
            "node": node,
            "role": self.role,
            "rung": self.rung_index,
            "model": last.model if last else None,
            "status": self.status,
            "escalated": self.escalated,
            "reason": last.reason if last else None,
            "attempts": [
                {
                    "rung": a.rung_index,
                    "selector": a.selector,
                    "model": a.model,
                    "status": a.status,
                    "reason": a.reason,
                    "elapsed_ms": a.elapsed_ms,
                }
                for a in self.attempts
            ],
        }


def _is_enabled() -> bool:
    try:
        from utils.server_config import MODEL_ROLES_ENABLED

        return bool(MODEL_ROLES_ENABLED)
    except Exception:
        return False


def _resolve_spec(role: str | ModelRoleSpec) -> ModelRoleSpec:
    if isinstance(role, ModelRoleSpec):
        return role
    if not _is_enabled():
        return _FALLBACK_SPEC
    try:
        from core_graph.model_roles.registry import get_model_role

        spec = get_model_role(role)
    except Exception:
        logger.warning("model_roles.ladder: registry lookup failed for role=%s", role, exc_info=True)
        spec = None
    return spec or _FALLBACK_SPEC


def _start_rung(spec: ModelRoleSpec, *, state: Mapping[str, Any] | None, min_rung: int) -> int:
    advance = 0
    for cond in spec.entry_conditions:
        try:
            if cond.holds(state):
                advance += cond.advance
        except Exception:
            logger.debug("model_roles.ladder: entry_condition eval failed", exc_info=True)
    start = max(min_rung, advance)
    return min(start, max(len(spec.ladder) - 1, 0))


async def _fold_usage(token_usage: dict | None, raw: Any, model_name: str) -> dict | None:
    """Fold token usage from a raw LLM response.

    Guarded: this is telemetry, not control flow. A malformed response that
    breaks ``extract_token_usage`` must not discard an otherwise-valid attempt
    result or the accumulator built up by earlier rungs — so on failure it
    logs and returns ``token_usage`` unchanged rather than propagating.
    """
    from utils.telemetry import extract_token_usage

    from core_graph.node.helpers import fold_token_usage

    try:
        usage = extract_token_usage(raw)
        return fold_token_usage(token_usage, usage, model_name)
    except Exception as exc:
        logger.debug("model_roles.ladder: token usage extraction failed: %s", exc, exc_info=True)
        return token_usage


async def run_role_ladder(
    role: str | ModelRoleSpec,
    *,
    attempt: Callable[[Any], Awaitable[Any]],
    state: Mapping[str, Any] | None = None,
    extract: Callable[[Any], Any] | None = None,
    validate: Callable[[Any], str | None] | None = None,
    fallback_llm: Any = None,
    min_rung: int = 0,
    token_usage: dict | None = None,
    node: str = "",
) -> LadderResult:
    """Walk a role's escalation ladder. See module docstring for the algorithm."""
    from core_graph.model_roles.resolver import resolve_role_llm

    spec = _resolve_spec(role)
    validator: Validation | None = None
    if validate is None:
        validator = spec.validate

    rung_idx = _start_rung(spec, state=state, min_rung=min_rung)
    result = LadderResult(token_usage=token_usage, role=spec.role_id)

    n_rungs = len(spec.ladder)
    escalated_from_start = False
    last_llm: Any = None  # tracks whether the last resolved rung already *was* fallback_llm

    while rung_idx < n_rungs:
        rung: Rung = spec.ladder[rung_idx]
        for _ in range(max(rung.max_attempts, 1)):
            t0 = time.monotonic()
            try:
                llm, model_name = await resolve_role_llm(rung.selector, fallback=fallback_llm)
                last_llm = llm
            except Exception as exc:
                logger.warning("model_roles.ladder: resolve_role_llm failed for role=%s rung=%d: %s", spec.role_id, rung_idx, exc)
                result.attempts.append(
                    RungAttempt(rung_index=rung_idx, selector=rung.selector, model="unknown",
                                status="error", reason=f"exception:{type(exc).__name__}",
                                elapsed_ms=int((time.monotonic() - t0) * 1000))
                )
                break

            try:
                raw = await attempt(llm)
            except Exception as exc:
                result.token_usage = await _fold_usage(result.token_usage, None, model_name)
                result.attempts.append(
                    RungAttempt(rung_index=rung_idx, selector=rung.selector, model=model_name,
                                status="error", reason=f"exception:{type(exc).__name__}",
                                elapsed_ms=int((time.monotonic() - t0) * 1000))
                )
                if not spec.escalate_on_exception:
                    result.value = None
                    result.llm = llm
                    result.rung_index = rung_idx
                    result.status = "exhausted"
                    return await _apply_terminal(spec, result, attempt, fallback_llm, extract, last_llm)
                continue

            result.token_usage = await _fold_usage(result.token_usage, raw, model_name)

            value = extract(raw) if extract is not None else raw
            reason = validator.check(value) if validator is not None else (validate(value) if validate is not None else None)

            elapsed = int((time.monotonic() - t0) * 1000)
            if reason is None:
                result.attempts.append(
                    RungAttempt(rung_index=rung_idx, selector=rung.selector, model=model_name,
                                status="ok", elapsed_ms=elapsed)
                )
                result.value = value
                result.llm = llm
                result.rung_index = rung_idx
                result.escalated = escalated_from_start
                result.status = "ok"
                return result

            result.attempts.append(
                RungAttempt(rung_index=rung_idx, selector=rung.selector, model=model_name,
                            status="invalid", reason=reason, elapsed_ms=elapsed)
            )
            if not spec.escalate_on_invalid:
                result.value = value
                result.llm = llm
                result.rung_index = rung_idx
                result.status = "exhausted"
                return await _apply_terminal(spec, result, attempt, fallback_llm, extract)

        rung_idx += 1
        escalated_from_start = True

    result.status = "exhausted"
    result.rung_index = max(n_rungs - 1, 0)
    result.escalated = escalated_from_start
    return await _apply_terminal(spec, result, attempt, fallback_llm, extract, last_llm)


async def resolve_role_first_rung(role: str, *, fallback: Any = None) -> tuple[Any, str]:
    """Resolve the first rung of ``role``'s ladder — selection only, no attempt/escalation.

    For call sites with nothing to validate (chat, builder, step_resolver,
    single-shot agent-loop calls): ``GraphRuntimeContext.llm_for_role`` and
    the agent-loop ``role:<id>`` selector (§8.1) both go through this.
    """
    from core_graph.model_roles.resolver import resolve_role_llm

    spec = _resolve_spec(role)
    selector = spec.ladder[0].selector if spec.ladder else "core"
    return await resolve_role_llm(selector, fallback=fallback)


async def _apply_terminal(
    spec: ModelRoleSpec, result: LadderResult, attempt, fallback_llm, extract=None, last_llm: Any = None
) -> LadderResult:
    """Run the spec's terminal_fallback once the ladder is exhausted."""
    if spec.terminal_fallback == "none":
        result.value = None
        return result
    if spec.terminal_fallback == "error":
        result.value = None
        return result
    # "ctx_llm" — one final attempt against fallback_llm (never re-validated).
    if fallback_llm is None:
        result.value = None
        return result
    if last_llm is not None and last_llm is fallback_llm:
        # The exhausted rung already resolved to fallback_llm (e.g. the
        # single-rung "core" ladder used when the flag is off / spec absent)
        # — retrying the identical client would be a redundant duplicate
        # call, not a real fallback. Skip straight to "exhausted, no value".
        result.value = None
        return result
    t0 = time.monotonic()
    try:
        raw = await attempt(fallback_llm)
    except Exception as exc:
        result.attempts.append(
            RungAttempt(rung_index=result.rung_index, selector="ctx_llm", model="ctx_llm",
                        status="error", reason=f"exception:{type(exc).__name__}",
                        elapsed_ms=int((time.monotonic() - t0) * 1000))
        )
        result.value = None
        result.llm = fallback_llm
        return result
    model_name = str(getattr(fallback_llm, "model", getattr(fallback_llm, "model_name", "ctx_llm")))
    result.token_usage = await _fold_usage(result.token_usage, raw, model_name)
    result.attempts.append(
        RungAttempt(rung_index=result.rung_index, selector="ctx_llm", model=model_name,
                    status="ok", elapsed_ms=int((time.monotonic() - t0) * 1000))
    )
    result.value = extract(raw) if extract is not None else raw
    result.llm = fallback_llm
    return result
