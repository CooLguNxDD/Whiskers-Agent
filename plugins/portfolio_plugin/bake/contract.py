"""The single quality contract for a baked portfolio layout.

One implementation, used identically by the MCP tool ``bake_portfolio_for_job``
and by ``agents/bake.py::run_bake_agent``. Previously each had its own bar: the
agent post-gated and downgraded to ``partial``, the tool gated not at all, and
``ship_best`` forced ``status="ok"`` onto a layout the jury had rejected three
times. Two contracts for one operation means the answer to "is this page good
enough to send a recruiter" depended on which door you came through.

Violations are split into **blocking** (the page should not ship) and warn-only
(recorded, surfaced, but not fatal). Warn-only entries are candidates for
promotion once there is soak data behind them — see ``_WARN_ONLY``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.bake.contract")

# Codes recorded but not fatal.
#
# band_coverage_lt is unverified against floor layouts.
# unknown_block_dropped is a real defect but not worth withholding an otherwise
#   good page over.
# ungrounded_block CANNOT be authoritative yet: ``_sourceRefs`` is popped from
#   every block (compose/custom_layout.py, compose/context_enrich.py) before the
#   layout reaches this gate, so the check sees no refs on any bake and would
#   reject all of them. Promote to blocking once refs are durable in the
#   per-block sidecar; until then it is a signal, not a verdict.
_WARN_ONLY = frozenset(
    {
        "unknown_block_dropped",
        "band_coverage_lt",
        "ungrounded_block",
        # Display copy should reframe inventory summary; warn when fish blurb
        # is still a raw summary prefix dump (soak before promoting to block).
        "raw_summary_dump",
    }
)

# Distinct DAG bands a bake-grade page should span (hero + impact + projects...).
MIN_BAND_COVERAGE = 3


@dataclass(frozen=True)
class Violation:
    """One failed check.

    ``legacy`` carries the older ``layout_quality_ok`` string form (e.g.
    ``block_count_3_lt_6``) for the structural checks, so both the full
    contract and the structural-only shim share one rule site.
    """

    code: str
    detail: str = ""
    blocking: bool = True
    legacy: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert the violation into a JSON-serializable dictionary."""
        return {"code": self.code, "detail": self.detail, "blocking": self.blocking}


@dataclass
class QualityReport:
    """Outcome of the contract for one layout."""

    passed: bool = True
    violations: list[Violation] = field(default_factory=list)
    dimensions: dict[str, Any] = field(default_factory=dict)
    score: float | None = None

    @property
    def blocking(self) -> list[Violation]:
        """Only the violations that should stop a bake from shipping."""
        return [v for v in self.violations if v.blocking]

    def codes(self) -> list[str]:
        """Extract the list of violation codes."""
        return [v.code for v in self.violations]

    def to_dict(self) -> dict[str, Any]:
        """Convert the quality report into a JSON-serializable dictionary."""
        return {
            "passed": self.passed,
            "score": self.score,
            "violations": [v.to_dict() for v in self.violations],
            "dimensions": self.dimensions,
        }


def _add(report: QualityReport, code: str, detail: str = "", legacy: str = "") -> None:
    """Append a violation, deriving blocking-ness from the warn-only set."""
    report.violations.append(
        Violation(
            code=code,
            detail=detail,
            blocking=code not in _WARN_ONLY,
            legacy=legacy or code,
        )
    )


def _structural_violations(
    report: QualityReport,
    layout: dict[str, Any],
    blocks: list[dict],
    goal_class: str,
) -> None:
    """Min block count / type diversity / template-fallback bar.

    The subset that applies to any layout regardless of how it was produced —
    shared with the legacy ``layout_quality_ok`` shim.
    """
    from plugins.portfolio_plugin.compose.recipes import MIN_BLOCK_TYPES, min_blocks_for

    types = {str(b.get("type") or "") for b in blocks}
    types.discard("")
    need = min_blocks_for(goal_class)
    report.dimensions.update(
        {"block_count": len(blocks), "block_types": len(types), "min_blocks": need}
    )
    if len(blocks) < need:
        _add(
            report,
            "block_count_lt",
            f"{len(blocks)} < {need}",
            legacy=f"block_count_{len(blocks)}_lt_{need}",
        )
    if goal_class in ("bake_for_job", "redesign") and len(types) < MIN_BLOCK_TYPES:
        _add(
            report,
            "block_types_lt",
            f"{len(types)} < {MIN_BLOCK_TYPES}",
            legacy=f"block_types_{len(types)}_lt_{MIN_BLOCK_TYPES}",
        )

    meta = layout.get("meta") if isinstance(layout.get("meta"), dict) else {}
    if str(meta.get("mode") or "") == "template":
        _add(report, "flat_template_fallback", "meta.mode == template")


def structural_quality(
    layout: dict[str, Any] | None, *, goal_class: str
) -> tuple[bool, list[str]]:
    """Structural-only bar in the legacy ``(ok, errors)`` shape.

    Backs ``compose.recipes.layout_quality_ok``. Deliberately excludes schema
    validity, grounding and jury — callers such as the draft-passthrough check
    and the specialist validator ask only "is this page substantial enough",
    not "is this fit to send a recruiter". Use ``assess_bake_quality`` for that.
    """
    report = QualityReport()
    if not isinstance(layout, dict):
        return False, ["layout_missing"]
    blocks_in = layout.get("blocks")
    if not isinstance(blocks_in, list) or not blocks_in:
        return False, ["blocks_empty"]
    blocks = [b for b in blocks_in if isinstance(b, dict)]
    _structural_violations(report, layout, blocks, goal_class)
    errs = [v.legacy for v in report.violations if v.blocking]
    return (not errs), errs


def _grounding_violations(report: QualityReport, blocks: list[dict]) -> None:
    """Flag blocks whose type requires source refs but carry none.

    Refs live on the in-flight ``_sourceRefs`` key, which is popped before
    persistence — so this only sees them pre-pop. Absent catalog data, skip
    rather than fail: an unavailable catalog must not fail every bake.
    """
    try:
        from plugins.portfolio_plugin.schema.block_catalog import get_block_catalog

        catalog = get_block_catalog()
    except Exception as exc:
        logger.debug("contract: block catalog unavailable, skipping grounding: %s", exc)
        return

    ungrounded: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        feat = catalog.get(str(b.get("type") or ""))
        if feat is None:
            continue
        policy = getattr(feat, "source_refs", "not_required")
        if policy == "not_required":
            continue
        if policy == "required_if_authored" and getattr(feat, "grounding", "") != "authored":
            continue
        if not b.get("_sourceRefs"):
            ungrounded.append(str(b.get("id") or b.get("type") or "?"))

    report.dimensions["ungrounded_blocks"] = len(ungrounded)
    if ungrounded:
        _add(
            report,
            "ungrounded_block",
            f"{len(ungrounded)} block(s) require source refs and have none: "
            + ", ".join(ungrounded[:8]),
        )


def _raw_summary_dump_violations(report: QualityReport, blocks: list[dict]) -> None:
    """Warn when fish blurbs / cards look like inventory summary paste.

    Inventory summary is planner context; display copy is authored. Detecting
    markdown chrome and unreframed long blurbs keeps the zero-paste contract
    visible without withholding an otherwise good page (warn-only).
    """
    import re

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip()).lower()

    dumps: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        btype = str(b.get("type") or "")
        if btype == "fishTank":
            props = b.get("props") if isinstance(b.get("props"), dict) else {}
            fish = props.get("fish") if isinstance(props.get("fish"), list) else []
            for f in fish:
                if not isinstance(f, dict):
                    continue
                blurb = str(f.get("blurb") or "").strip()
                desc = str(f.get("description") or "").strip()
                if not blurb or len(blurb) < 40:
                    continue
                # Markdown inventory chrome or long unreframed dump.
                if blurb.lstrip().startswith(">") or "**" in blurb[:40]:
                    dumps.append(str(f.get("slug") or "?"))
                    continue
                if " — " not in blurb and " · " not in blurb and len(blurb) >= 120:
                    dumps.append(str(f.get("slug") or "?"))
                    continue
                if blurb and desc and len(blurb) >= 40:
                    bn, dn = _norm(blurb), _norm(desc)
                    if dn.startswith(bn) and len(dn) > len(bn) + 20:
                        if " — " not in blurb and " · " not in blurb:
                            dumps.append(str(f.get("slug") or "?"))
        elif btype == "card":
            props = b.get("props") if isinstance(b.get("props"), dict) else {}
            body = str(props.get("body") or "").strip()
            if body.lstrip().startswith(">") and len(body) >= 80:
                dumps.append(str(b.get("id") or "card"))
    report.dimensions["raw_summary_dumps"] = len(dumps)
    if dumps:
        _add(
            report,
            "raw_summary_dump",
            f"{len(dumps)} display field(s) look like inventory paste: "
            + ", ".join(dumps[:8]),
        )


def assess_bake_quality(
    layout: dict[str, Any] | None,
    *,
    goal_class: str = "bake_for_job",
    jury: dict[str, Any] | None = None,
    agent_result: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
) -> QualityReport:
    """Assess whether a layout is fit to ship. Never raises.

    ``agent_result`` is the ``run_layout_agent`` envelope; its ``ship_best``
    flag is the authoritative signal that the jury never passed.
    """
    report = QualityReport()

    if not isinstance(layout, dict):
        _add(report, "layout_missing", "no layout dict")
        report.passed = False
        return report

    blocks_in = layout.get("blocks")
    if not isinstance(blocks_in, list) or not blocks_in:
        _add(report, "blocks_empty", "layout has no blocks")
        report.passed = False
        return report

    blocks = [b for b in blocks_in if isinstance(b, dict)]

    # --- schema validity, and what validation silently removed ---------------
    try:
        from plugins.portfolio_plugin.schema.ui_layout_schema import validate_layout

        validated, verrs = validate_layout(layout)
        if verrs or validated is None:
            _add(report, "schema_invalid", "; ".join(str(e) for e in (verrs or []))[:400])
        elif isinstance(validated.get("blocks"), list):
            # validate_layout filters unknown block types rather than rejecting
            # them, so a hallucinated type vanishes with no trace. Surface it.
            dropped = len(blocks) - len(validated["blocks"])
            if dropped > 0:
                kept = {str(b.get("id") or "") for b in validated["blocks"] if isinstance(b, dict)}
                names = [
                    str(b.get("type") or "?") for b in blocks if str(b.get("id") or "") not in kept
                ]
                _add(
                    report,
                    "unknown_block_dropped",
                    f"{dropped} block(s) filtered by schema: " + ", ".join(names[:8]),
                )
    except Exception as exc:
        logger.debug("contract: validate_layout raised: %s", exc)
        _add(report, "schema_invalid", f"{type(exc).__name__}: {exc}"[:400])

    # --- structural bar -------------------------------------------------------
    _structural_violations(report, layout, blocks, goal_class)

    # --- band coverage --------------------------------------------------------
    try:
        from plugins.portfolio_plugin.compose.dag import DAG_LEVEL_BY_TYPE

        types = {str(b.get("type") or "") for b in blocks}
        types.discard("")
        bands = {DAG_LEVEL_BY_TYPE[t][0] for t in types if t in DAG_LEVEL_BY_TYPE}
        report.dimensions["band_coverage"] = len(bands)
        if goal_class in ("bake_for_job", "redesign") and len(bands) < MIN_BAND_COVERAGE:
            _add(report, "band_coverage_lt", f"{len(bands)} < {MIN_BAND_COVERAGE} DAG bands")
    except Exception as exc:
        logger.debug("contract: band coverage skipped: %s", exc)

    # --- grounding ------------------------------------------------------------
    _grounding_violations(report, blocks)

    # --- zero-paste: fish blurb must not be raw inventory summary prefix ------
    _raw_summary_dump_violations(report, blocks)

    # --- jury -----------------------------------------------------------------
    jury = jury if isinstance(jury, dict) else (agent_result or {}).get("jury")
    composite = (jury or {}).get("composite") if isinstance(jury, dict) else None
    if composite is not None:
        try:
            report.score = float(composite)
        except (TypeError, ValueError):
            report.score = None
    if report.score is not None:
        threshold = 7.5
        if isinstance(cfg, dict):
            try:
                threshold = float(cfg.get("jury_threshold", threshold))
            except (TypeError, ValueError):
                pass
        report.dimensions["jury_threshold"] = threshold
        if report.score < threshold:
            _add(report, "jury_below_threshold", f"{report.score} < {threshold}")

    # ship_best is set by run_layout_agent when no round ever passed the jury.
    # It is the difference between "good page" and "least bad of three".
    if isinstance(agent_result, dict) and agent_result.get("ship_best"):
        rounds = len(agent_result.get("jury_history") or [])
        _add(report, "jury_never_passed", f"ship_best after {rounds} round(s)")

    report.passed = not report.blocking
    return report
