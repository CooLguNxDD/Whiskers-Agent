"""Build fishTank blocks from portfolio projects (pure / sync / no-DB).

Lazy in-function imports dodge the composer cycle (same pattern as floor.py).
"""

from __future__ import annotations

import hashlib
import logging
from calendar import isleap
from datetime import date
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.compose.fish")

_DOMAIN = frozenset({"ai", "devops", "mobile", "platform"})


def _clamp01(v: float) -> float:
    if v != v:  # NaN
        return 0.5
    return max(0.0, min(1.0, float(v)))


def _hash_unit(slug: str) -> float:
    h = hashlib.sha256(slug.encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _fractional_year(value: Any) -> float | None:
    """Parse an ISO date (str/date) into a fractional year, e.g. 2025.67."""
    if value is None:
        return None
    try:
        d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None
    days_in_year = 366 if isleap(d.year) else 365
    return d.year + (d.timetuple().tm_yday - 1) / days_in_year


def _project_year_bounds(proj: dict[str, Any]) -> tuple[float | None, float | None]:
    """(startYear, effectiveYear) for one project — effectiveYear is endYear when
    known, else "now" for an ongoing project with only a start date, else None."""
    start = _fractional_year(proj.get("started_on"))
    end = _fractional_year(proj.get("ended_on"))
    if end is not None:
        return start, end
    if start is not None:
        return start, _fractional_year(date.today())
    return None, None


def _tank_time_span(projects: list[dict[str, Any]]) -> tuple[float, float] | None:
    """Fractional-year (min, max) across all dated projects.

    None when fewer than 2 projects carry a date — a single dated project
    isn't enough to place the rest of an undated tank on a meaningful scale.
    """
    years: list[float] = []
    dated_count = 0
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        start, effective = _project_year_bounds(proj)
        if start is None and effective is None:
            continue
        dated_count += 1
        if start is not None:
            years.append(start)
        if effective is not None:
            years.append(effective)
    if dated_count < 2 or not years:
        return None
    return min(years), max(years)


def _coerce_time_span(value: Any) -> tuple[float, float] | None:
    """Parse a ``{"min": float, "max": float}`` dict into a bounds tuple."""
    if not isinstance(value, dict):
        return None
    try:
        lo = float(value["min"])
        hi = float(value["max"])
    except (KeyError, TypeError, ValueError):
        return None
    if hi < lo:
        return None
    return lo, hi


def _resolve_roster(
    projects: list[dict[str, Any]] | None,
    *,
    highlight_slugs: list[str] | None,
    job_tokens: set[str] | None,
    roster_limit: int | None,
    min_relevance: float | None,
) -> list[dict[str, Any]]:
    """Quality-filter, optionally JD-trim, then hard-cap at 40.

    Defaults to full inventory. JD-trimming (via roster_limit) is tag-blind;
    only chosen highlight slugs bypass the JD relevancy bar.
    """
    from plugins.portfolio_plugin.compose.quality import filter_projects_for_layout

    worthy = filter_projects_for_layout(projects or [])
    if roster_limit is not None and job_tokens:
        from plugins.portfolio_plugin.compose.job_tailor import rank_projects_for_tank

        worthy = rank_projects_for_tank(
            worthy,
            job_tokens,
            limit=roster_limit,
            min_score=min_relevance or 0.0,
            always_slugs=highlight_slugs,
        )
    return worthy[:40]


def build_fish_specimens(
    projects: list[dict[str, Any]] | None,
    *,
    highlight_slugs: list[str] | None = None,
    job_tokens: set[str] | None = None,
    body_by_slug: dict[str, str] | None = None,
    time_span: dict[str, Any] | None = None,
    roster_limit: int | None = None,
    min_relevance: float | None = None,
    _roster: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """Map filtered projects → fish specimen dicts (all numerics in [0,1]).

    Uses authored display copy. `time_span` freezes chronological depth scale
    for stable ask-mode patches. JD-trimming is opt-in via `roster_limit`.
    """
    from plugins.portfolio_plugin.compose.display_copy import author_display_copy
    from plugins.portfolio_plugin.compose.quality import project_metrics_and_links
    from plugins.portfolio_plugin.compose.ranking import (
        _content_depth,
        _infer_domain,
        _priority_bias,
        _project_family,
    )

    hl = {str(s).lower() for s in (highlight_slugs or []) if s}
    bodies = {str(k).lower(): v for k, v in (body_by_slug or {}).items() if v}
    roster = _roster if _roster is not None else _resolve_roster(
        projects,
        highlight_slugs=highlight_slugs,
        job_tokens=job_tokens,
        roster_limit=roster_limit,
        min_relevance=min_relevance,
    )
    frozen = _coerce_time_span(time_span)
    span_bounds = frozen if frozen is not None else _tank_time_span(roster)
    family_index: dict[str, int] = {}
    next_school = 0
    out: list[dict[str, Any]] = []

    for i, proj in enumerate(roster):
        if not isinstance(proj, dict):
            continue
        slug = str(proj.get("slug") or f"proj-{i}").strip()
        if not slug:
            continue
        slug_l = slug.lower()
        highlighted = slug_l in hl

        domain = _infer_domain(proj) or "platform"
        if domain not in _DOMAIN:
            domain = "platform"

        depth_raw = _content_depth(proj)
        size = _clamp01((depth_raw + 0.6) / 1.45)

        # Chronology drives depth when the tank has a real time span (>=2
        # dated projects); newest = shallow, oldest = deep. sort_order stays
        # the fallback for undated projects, so a tank with no discovered
        # dates behaves exactly as before.
        start_year, effective_year = _project_year_bounds(proj)
        if span_bounds is not None and effective_year is not None:
            span_min, span_max = span_bounds
            span = span_max - span_min
            depth = _clamp01(1.0 - (effective_year - span_min) / span) if span > 0 else 0.0
        else:
            # sort_order 0 is valid top rank — never use ``or 50``
            raw_so = proj.get("sort_order")
            try:
                so = 50 if raw_so is None else int(raw_so)
            except (TypeError, ValueError):
                logger.debug(
                    "fish: invalid sort_order %r for slug %s, defaulting to 50", raw_so, slug
                )
                so = 50
            depth = _clamp01(so / 20.0)
        if highlighted:
            depth = _clamp01(depth * 0.35)

        bias = _priority_bias(proj)
        glow = _clamp01((bias + 0.7) / 1.6)
        if highlighted:
            glow = max(glow, 0.85)

        fam = _project_family(proj)
        if fam not in family_index:
            family_index[fam] = next_school
            next_school = min(15, next_school + 1)
        school = family_index[fam]

        preferred = bodies.get(slug_l)
        blurb = author_display_copy(
            proj,
            form="fish_blurb",
            job_tokens=job_tokens,
            preferred_body=preferred,
        )
        description = author_display_copy(
            proj,
            form="description",
            job_tokens=job_tokens,
            preferred_body=preferred,
        )
        metrics, links = project_metrics_and_links(proj)
        metrics_out = [{"label": m.label, "value": m.value} for m in metrics[:6]]
        link_out = None
        if links:
            link_out = {"label": links[0].label, "href": links[0].href}

        tags = list(proj.get("tags") or []) if isinstance(proj.get("tags"), list) else []
        tags = [str(t) for t in tags if t][:24]

        detail_ref = None
        try:
            from plugins.portfolio_plugin.compose.block_builder import (
                _source_refs_from_projects,
            )

            refs = _source_refs_from_projects([proj])
            if refs:
                detail_ref = refs[0]
        except Exception as exc:
            logger.debug("fish detail_ref skipped: %s", exc)

        speed = _clamp01(0.35 + 0.3 * _hash_unit(slug) + 0.25 * size)

        specimen: dict[str, Any] = {
            "slug": slug,
            "title": str(proj.get("name") or slug)[:80],
            "species": domain,
            "size": size,
            "depth": depth,
            "speed": speed,
            "glow": glow,
            "school": school,
            "tags": tags,
            "metrics": metrics_out,
        }
        # Optional fields are OMITTED, never null. CatPortfolio's Zod mirror
        # types these as `.optional()`, which accepts `undefined` but rejects
        # `null` — and one bad specimen fails the whole discriminatedUnion, so
        # the SPA silently falls back to the master snapshot for the entire
        # baked page. Keep this shape in sync with FishSpecimen in
        # CatPortfolio src/content/schema.ts.
        if blurb:
            specimen["blurb"] = blurb
        if description:
            specimen["description"] = description
        if detail_ref:
            specimen["detailRef"] = detail_ref
        if link_out:
            specimen["link"] = link_out
        if start_year is not None:
            specimen["startYear"] = round(start_year, 2)
        end_year = _fractional_year(proj.get("ended_on"))
        if end_year is not None:
            specimen["endYear"] = round(end_year, 2)

        out.append(specimen)
    return out


def build_fish_tank_block(
    projects: list[dict[str, Any]] | None,
    *,
    highlight_slugs: list[str] | None = None,
    block_id: str = "fish-tank-1",
    title: str | None = None,
    curation_label: str | None = None,
    tank_theme: str | None = None,
    job_tokens: set[str] | None = None,
    body_by_slug: dict[str, str] | None = None,
    time_span: dict[str, Any] | None = None,
    roster_limit: int | None = None,
    min_relevance: float | None = None,
) -> dict[str, Any] | None:
    """Build a fishTank block dict, or None when no specimens.

    Opts into frozen time scale and JD-scoped trimming when args are provided.
    """
    frozen = _coerce_time_span(time_span)
    roster = _resolve_roster(
        projects,
        highlight_slugs=highlight_slugs,
        job_tokens=job_tokens,
        roster_limit=roster_limit,
        min_relevance=min_relevance,
    )
    span_bounds = frozen or _tank_time_span(roster)
    if span_bounds:
        time_span_arg = {"min": span_bounds[0], "max": span_bounds[1]}
    else:
        time_span_arg = None

    fish = build_fish_specimens(
        projects,
        highlight_slugs=highlight_slugs,
        job_tokens=job_tokens,
        body_by_slug=body_by_slug,
        time_span=time_span_arg,
        roster_limit=None,
        min_relevance=None,
        _roster=roster,
    )
    if not fish:
        return None
    props: dict[str, Any] = {
        "renderer": "webgl",
        "fish": fish,
        "highlightSlugs": list(highlight_slugs or [])[:20],
    }
    if title:
        props["title"] = str(title)
    if curation_label:
        props["curationLabel"] = str(curation_label)
    if tank_theme:
        props["tankTheme"] = str(tank_theme)
    if span_bounds is not None:
        props["timeSpan"] = {"min": round(span_bounds[0], 2), "max": round(span_bounds[1], 2)}
    return {"type": "fishTank", "id": block_id, "props": props}
