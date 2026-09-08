"""Ask-mode fishpool recommendation helpers.

Pure module (no I/O, no DB, no async, no LLM) providing field-weighted question
scoring and candidate ranking for ask-mode fishpool recommendations.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


def score_question(project: dict[str, Any], tokens: set[str]) -> float:
    """Field-weighted ask score: slug/name/tags beat a long summary.

    Bake ranking still uses ``rank_projects_by_query``. This is only the
    single-project focus pick, so a thin GitHub row named Fisoul must beat a
    dense employer/OSS row that happens to contain "the" and "about".
    """
    if not tokens or not isinstance(project, dict):
        return 0.0
    slug = str(project.get("slug") or "").strip().lower()
    slug_words = {slug, *slug.replace("-", " ").replace("_", " ").split()}
    name = str(project.get("name") or "").lower()
    tags = {str(t).strip().lower() for t in (project.get("tags") or []) if t}
    summary = str(project.get("summary") or "").lower()
    score = 0.0
    for tok in tokens:
        if tok == slug or tok in slug_words:
            score += 5.0
        elif re.search(rf"\b{re.escape(tok)}\b", name):
            score += 3.0
        elif tok in tags:
            score += 3.0
        elif tok and tok in slug:
            score += 2.0
        elif re.search(rf"\b{re.escape(tok)}\b", summary):
            score += 1.0
    return score


def _fish_blurb_max_chars() -> int:
    """Read fish_blurb_max_chars lazily from plugin SETTINGS."""
    try:
        from plugins.portfolio_plugin.plugin_config import SETTINGS

        dc = SETTINGS.get("display_copy") if isinstance(SETTINGS, dict) else None
        if isinstance(dc, dict) and "fish_blurb_max_chars" in dc:
            return int(dc["fish_blurb_max_chars"])
    except (ImportError, TypeError, ValueError, KeyError, AttributeError):
        return 200
    return 200


def _clip_blurb(summary: str, max_chars: int) -> str:
    """Clip summary on word boundary to max_chars without dumping full inventory summary."""
    s = (summary or "").strip()
    if not s:
        return ""
    if len(s) <= max_chars:
        return s
    clipped = s[:max_chars]
    last_space = clipped.rfind(" ")
    if last_space > 0:
        clipped = clipped[:last_space]
    return clipped.rstrip(".,;:- ")


def _parse_date_num(val: Any) -> float | None:
    """Parse date or date string into float year representation."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, date):
        days = 366 if val.year % 4 == 0 and (val.year % 100 != 0 or val.year % 400 == 0) else 365
        return val.year + (val.timetuple().tm_yday - 1) / float(days)
    s = str(val).strip()
    if not s:
        return None
    try:
        d = date.fromisoformat(s[:10])
        days = 366 if d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0) else 365
        return d.year + (d.timetuple().tm_yday - 1) / float(days)
    except (ValueError, TypeError):
        try:
            return float(s[:4])
        except (ValueError, TypeError):
            return None


def _recency_key(project: dict[str, Any]) -> tuple[int, float, float, int]:
    """Sort key for recency fallback: ended_on desc, started_on desc, sort_order asc.

    ended_on is None means ONGOING = newest, sorting to the top.
    """
    raw_ended = project.get("ended_on")
    if raw_ended is None or str(raw_ended).strip() == "":
        is_ongoing = 1
        ended_val = 0.0
    else:
        is_ongoing = 0
        parsed = _parse_date_num(raw_ended)
        ended_val = parsed if parsed is not None else -1e9

    raw_started = project.get("started_on")
    started_parsed = _parse_date_num(raw_started)
    started_val = started_parsed if started_parsed is not None else -1e9

    raw_so = project.get("sort_order")
    try:
        so = 50 if raw_so is None else int(raw_so)
    except (TypeError, ValueError):
        so = 50

    return (-is_ongoing, -ended_val, -started_val, so)


def _determine_reason(project: dict[str, Any], tokens: set[str], score: float) -> str:
    """Determine deterministic reason for recommendation match."""
    if score <= 0.0 or not tokens:
        return "recent work"
    slug = str(project.get("slug") or "").strip().lower()
    slug_words = {slug, *slug.replace("-", " ").replace("_", " ").split()}
    name = str(project.get("name") or "").lower()
    tags = {str(t).strip().lower() for t in (project.get("tags") or []) if t}
    summary = str(project.get("summary") or "").lower()

    tag_match = any(tok in tags for tok in tokens)
    name_match = any(
        tok == slug
        or tok in slug_words
        or bool(re.search(rf"\b{re.escape(tok)}\b", name))
        or (bool(tok) and tok in slug)
        for tok in tokens
    )
    summary_match = any(bool(re.search(rf"\b{re.escape(tok)}\b", summary)) for tok in tokens)

    if tag_match:
        return "closest tag match"
    if name_match:
        return "closest name match"
    if summary_match:
        return "closest match"
    return "recent work"


def recommend_projects(
    rows: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    tokens: set[str],
    *,
    in_tank: set[str],
    limit: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(existing_recs, pool_recs) — near-miss, then ranked order, then recency."""
    if not rows:
        return ([], [])

    in_tank_set = {str(s).strip().lower() for s in (in_tank or set()) if str(s).strip()}
    blurb_cap = _fish_blurb_max_chars()

    # Deduplicate valid rows preserving order
    valid_rows: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for p in rows:
        if not isinstance(p, dict):
            continue
        slug = str(p.get("slug") or "").strip()
        if not slug:
            continue
        slug_l = slug.lower()
        if slug_l in seen_slugs:
            continue
        seen_slugs.add(slug_l)
        valid_rows.append(p)

    if not valid_rows:
        return ([], [])

    # Index ranked list positions
    ranked_positions: dict[str, int] = {}
    for i, p in enumerate(ranked or []):
        if not isinstance(p, dict):
            continue
        slug = str(p.get("slug") or "").strip().lower()
        if slug and slug not in ranked_positions:
            ranked_positions[slug] = i

    # Compute scores and categorize projects
    # Ordering priority (a project is placed by the FIRST rule that applies):
    # 1. score_question(project, tokens) > 0 — higher score first
    # 2. position in ranked (earlier = better)
    # 3. recency fallback — ended_on desc, then started_on desc, then sort_order asc.
    tier1: list[tuple[float, int, tuple[int, float, float, int], dict[str, Any]]] = []
    tier2: list[tuple[int, tuple[int, float, float, int], dict[str, Any]]] = []
    tier3: list[tuple[tuple[int, float, float, int], dict[str, Any]]] = []

    project_scores: dict[str, float] = {}
    for p in valid_rows:
        slug_l = str(p.get("slug") or "").strip().lower()
        score = score_question(p, tokens) if tokens else 0.0
        project_scores[slug_l] = score
        rec_key = _recency_key(p)

        if score > 0.0:
            rank_pos = ranked_positions.get(slug_l, 999999)
            tier1.append((-score, rank_pos, rec_key, p))
        elif slug_l in ranked_positions:
            tier2.append((ranked_positions[slug_l], rec_key, p))
        else:
            tier3.append((rec_key, p))

    tier1.sort(key=lambda item: (item[0], item[1], item[2]))
    tier2.sort(key=lambda item: (item[0], item[1]))
    tier3.sort(key=lambda item: item[0])

    ordered_projects: list[dict[str, Any]] = (
        [item[3] for item in tier1]
        + [item[2] for item in tier2]
        + [item[1] for item in tier3]
    )

    existing_recs: list[dict[str, Any]] = []
    pool_recs: list[dict[str, Any]] = []

    for p in ordered_projects:
        slug = str(p.get("slug") or "").strip()
        slug_l = slug.lower()
        score = project_scores.get(slug_l, 0.0)
        is_in_tank = slug_l in in_tank_set
        reason = _determine_reason(p, tokens, score)
        name = str(p.get("name") or slug)
        summary = str(p.get("summary") or "")
        blurb = _clip_blurb(summary, max_chars=blurb_cap)
        raw_tags = p.get("tags")
        tags = [str(t) for t in raw_tags if t] if isinstance(raw_tags, list) else []

        wire_dict: dict[str, Any] = {
            "slug": slug,
            "name": name,
            "blurb": blurb,
            "tags": tags,
            "reason": reason,
            "in_tank": is_in_tank,
        }

        if is_in_tank:
            if len(existing_recs) < limit:
                existing_recs.append(wire_dict)
        else:
            if len(pool_recs) < limit:
                pool_recs.append(wire_dict)

    return (existing_recs, pool_recs)
