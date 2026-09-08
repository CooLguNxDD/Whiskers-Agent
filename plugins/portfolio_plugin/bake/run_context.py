"""Typed error / timing accumulation for the portfolio bake pipeline.

The bake is a fall-through ladder of fail-open rungs. Historically every rung
swallowed its cause into a ``logger.warning`` and returned an empty error list,
so a caller who got a floor layout — or nothing at all — had no way to learn
why. ``BakeContext`` is the shared accumulator that carries the cause, the
stage it came from, and how long each stage took, all the way out to the tool
response and the durable ``portfolio_bake_runs`` row.

Stage functions returning ``StageResult`` arrive with the staged pipeline; this
module currently covers what the ladder needs to report on itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Bounds errors_json / stages_json growth in the durable run record.
MAX_MESSAGE_CHARS = 500


class StageStatus(str, Enum):
    """Outcome of one bake stage. ``str`` mixin so it serializes into JSONB."""

    OK = "ok"
    DEGRADED = "degraded"
    SKIPPED = "skipped"
    FAILED = "failed"


class BakeErrorCode:
    """Stable error-code vocabulary.

    Plain string constants rather than an ``Enum``: these land in JSONB and get
    grepped out of run rows by operators, so the wire value is the contract.
    """

    # resolve_job_posting_text — the three outbound hops
    RESOLVE_DETAIL_FAILED = "resolve_detail_failed"
    RESOLVE_FETCH_FAILED = "resolve_fetch_failed"
    RESOLVE_WEBSEARCH_FAILED = "resolve_websearch_failed"

    # compose ladder
    PREBUILT_INVALID = "prebuilt_invalid"
    EVIDENCE_PACK_FAILED = "evidence_pack_failed"
    AGENTIC_FAILED = "agentic_failed"
    AGENTIC_EMPTY = "agentic_empty"
    # Jury never passed; ship_best forced a best-loser layout through.
    AGENTIC_JURY_NEVER_PASSED = "agentic_jury_never_passed"
    FLOOR_FAILED = "floor_failed"
    FLOOR_EMPTY = "floor_empty"
    TEMPLATE_FAILED = "template_failed"
    TEMPLATE_EMPTY = "template_empty"

    # post-compose
    TAILOR_FAILED = "tailor_failed"
    FISH_TANK_FAILED = "fish_tank_failed"

    # persistence
    SHORT_ID_COLLISION = "short_id_collision"
    PERSIST_FAILED = "persist_failed"


class Timer:
    """Monotonic stopwatch reporting whole milliseconds."""

    __slots__ = ("_t0",)

    def __init__(self) -> None:
        self._t0 = time.monotonic()

    @property
    def ms(self) -> int:
        """Milliseconds elapsed since construction."""
        return int((time.monotonic() - self._t0) * 1000)


@dataclass(frozen=True)
class StageError:
    """One recorded failure, attributed to the stage that produced it."""

    stage: str
    code: str
    message: str = ""
    fatal: bool = False
    elapsed_ms: int | None = None

    @classmethod
    def from_exc(
        cls,
        stage: str,
        code: str,
        exc: BaseException,
        *,
        fatal: bool = False,
        elapsed_ms: int | None = None,
    ) -> StageError:
        """Build from a caught exception, keeping the type name in the message."""
        return cls(
            stage=stage,
            code=code,
            message=f"{type(exc).__name__}: {exc}",
            fatal=fatal,
            elapsed_ms=elapsed_ms,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form for the tool response and the run record."""
        out: dict[str, Any] = {
            "stage": self.stage,
            "code": self.code,
            "message": self.message[:MAX_MESSAGE_CHARS],
        }
        if self.fatal:
            out["fatal"] = True
        if self.elapsed_ms is not None:
            out["elapsed_ms"] = self.elapsed_ms
        return out


@dataclass
class BakeContext:
    """Mutable accumulator threaded through one bake.

    Stages never raise past their own boundary; they append here instead. The
    tool reads ``error_dicts()`` for its response and ``timings()`` /
    ``provenance`` for the durable run record.
    """

    company: str = ""
    role: str = ""
    tenant_id: int = 0
    errors: list[StageError] = field(default_factory=list)
    stage_ms: dict[str, int] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    # The LayoutPlan that produced the layout, when an agentic rung won. Rides
    # here rather than on the compose return tuple, whose shape has callers.
    plan: dict[str, Any] | None = None
    _timer: Timer = field(default_factory=Timer, repr=False)

    def add_error(
        self,
        stage: str,
        code: str,
        message: str = "",
        *,
        fatal: bool = False,
        elapsed_ms: int | None = None,
    ) -> None:
        """Record a failure without interrupting the ladder."""
        self.errors.append(
            StageError(
                stage=stage,
                code=code,
                message=message,
                fatal=fatal,
                elapsed_ms=elapsed_ms,
            )
        )

    def add_exc(
        self,
        stage: str,
        code: str,
        exc: BaseException,
        *,
        fatal: bool = False,
        elapsed_ms: int | None = None,
    ) -> None:
        """Record a caught exception against a stage."""
        self.errors.append(
            StageError.from_exc(stage, code, exc, fatal=fatal, elapsed_ms=elapsed_ms)
        )

    def mark(self, stage: str, elapsed_ms: int) -> None:
        """Record how long a stage took. Repeat calls for one stage sum."""
        self.stage_ms[stage] = self.stage_ms.get(stage, 0) + int(elapsed_ms)

    def stamp(self, **values: Any) -> None:
        """Merge provenance values, dropping ``None`` so stamps stay sparse."""
        for key, val in values.items():
            if val is not None:
                self.provenance[key] = val

    def error_dicts(self) -> list[dict[str, Any]]:
        """All recorded errors in JSON-safe form, oldest first."""
        return [e.to_dict() for e in self.errors]

    def codes(self) -> list[str]:
        """Just the error codes — cheap for log lines and telemetry labels."""
        return [e.code for e in self.errors]

    def timings(self) -> dict[str, int]:
        """Per-stage elapsed milliseconds."""
        return dict(self.stage_ms)

    @property
    def total_ms(self) -> int:
        """Wall-clock milliseconds since the context was created."""
        return self._timer.ms
