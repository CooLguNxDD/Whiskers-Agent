"""Deterministic post-compose job framing for portfolio bakes.

Job identity (company / role) lives in the public short_id (``?j=``) and in
layout **meta** only — never stuffed into hero tagline/pitch. Visible hero
stays the personal brand. Differentiation across JDs comes from card order,
block selection, and silent meta stamps.
"""

from __future__ import annotations

import hashlib
import logging
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.job_tailor")

def _safe_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.debug("job_tailor: invalid int %r, defaulting to %d", value, default)
        return default

@lru_cache(maxsize=512)
def _token_re(token: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(token)}\b")

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+.#-]{1,32}", re.I)

# Two-letter domain words the length>2 filter would drop. "ai project"
# must still hit a tag/name; substring match is too loose ("wait", "email").
_SHORT_TECH = frozenset({"ai", "ml", "go", "js", "ts", "ui", "db", "qa"})

# Dimming only reads as curation while highlights stay a minority of the tank.
_MAX_HIGHLIGHT_SLUGS = 5

# Generic titles the floor/agent often emit — retitle for this job.
_GENERIC_FLOW_TITLES = frozenset(
    {
        "agent system flow",
        "system flow",
        "architecture flow",
    }
)
_GENERIC_COMPARE_TITLES = frozenset(
    {
        "project contrast",
        "projects",
        "comparison",
    }
)

_DOMAIN_CUES: list[tuple[tuple[str, ...], str]] = [
    (("ai", "ml", "llm", "agent", "rag", "genai", "openai", "model"), "AI systems"),
    (("devops", "sre", "kubernetes", "k8s", "infra", "aws", "terraform", "platform"), "cloud infra"),
    (("mobile", "ios", "android", "react native", "flutter"), "mobile"),
    (("mcp", "tool", "gateway", "orchestr"), "agent tooling"),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for m in _TOKEN_RE.finditer(text or ""):
        tok = m.group(0).lower()
        if len(tok) > 2 or tok in _SHORT_TECH:
            out.add(tok)
    return out


def _domain_cue(job_text: str, role: str) -> str:
    blob = f"{role} {job_text}".lower()
    for keys, label in _DOMAIN_CUES:
        if any(k in blob for k in keys):
            return label
    return ""


def _job_brief_hash(job_text: str) -> str:
    raw = _norm(job_text).lower()[:2000].encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:16]


def _card_text_blob(card: dict) -> str:
    props = card.get("props") if isinstance(card.get("props"), dict) else {}
    parts = [
        str(props.get("title") or ""),
        str(props.get("summary") or props.get("description") or ""),
        str(props.get("subtitle") or ""),
    ]
    tags = props.get("tags") or []
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags if t)
    return " ".join(parts)


def _score_blob(blob: str, tokens: set[str]) -> float:
    if not tokens or not blob:
        return 0.0
    low = blob.lower()
    hit = 0
    for t in tokens:
        # Word-boundary matching prevents short roots like "api" inflating scores from inside unrelated words.
        if _token_re(t).search(low):
            hit += 1
    return float(hit)


def _project_blob(proj: dict) -> str:
    """Same text a project row contributes to any JD score — shared by
    highlight selection and roster ranking so they can't diverge."""
    tags = proj.get("tags") if isinstance(proj.get("tags"), list) else []
    return " ".join(
        [
            str(proj.get("name") or ""),
            str(proj.get("summary") or ""),
            *(str(t) for t in tags if t),
        ]
    )


def _reorder_cards_and_prose(blocks: list[dict], job_tokens: set[str]) -> list[dict]:
    """Stable-sort card blocks by JD lexical score; align matching prose-* after cards."""
    if not blocks or not job_tokens:
        return blocks

    card_indices = [i for i, b in enumerate(blocks) if isinstance(b, dict) and b.get("type") == "card"]
    if len(card_indices) < 2:
        return blocks

    cards = [(i, blocks[i]) for i in card_indices]
    scored = sorted(
        cards,
        key=lambda pair: (
            -_score_blob(_card_text_blob(pair[1]), job_tokens),
            pair[0],  # stable
        ),
    )
    reordered_cards = [b for _, b in scored]

    # Map preferred slug order from card ids like card-helix-ai
    slug_order: list[str] = []
    for c in reordered_cards:
        cid = str(c.get("id") or "")
        if cid.startswith("card-") and len(cid) > 5:
            slug_order.append(cid[5:])

    out = list(blocks)
    for new_i, old_i in enumerate(card_indices):
        out[old_i] = reordered_cards[new_i]

    # Reorder prose blocks that share prose-<slug> with cards (among themselves only)
    prose_indices = [
        i
        for i, b in enumerate(out)
        if isinstance(b, dict)
        and b.get("type") == "prose"
        and str(b.get("id") or "").startswith("prose-")
    ]
    if prose_indices and slug_order:
        prose_blocks = [out[i] for i in prose_indices]

        def prose_rank(b: dict) -> tuple[int, int]:
            pid = str(b.get("id") or "")
            slug = pid[6:] if pid.startswith("prose-") else pid
            try:
                return (slug_order.index(slug), 0)
            except ValueError:
                return (999, 0)

        prose_sorted = sorted(prose_blocks, key=prose_rank)
        for new_i, old_i in enumerate(prose_indices):
            out[old_i] = prose_sorted[new_i]

    return out


def job_tokens(company: str, role: str, job_text: str = "") -> set[str]:
    """Lexical token set for a posting — the shared job signal."""
    return _tokens(f"{_norm(role)} {_norm(company)} {_norm(job_text)}")


def matched_project_slugs(
    projects: list[dict] | None,
    tokens: set[str],
    *,
    limit: int = _MAX_HIGHLIGHT_SLUGS,
) -> list[str]:
    """Rank **projects** by JD lexical overlap; return the top slugs.

    Highlight slugs must be real ``portfolio_projects.slug`` values — the fish
    tank matches specimens by slug. Deriving them from card block ids does not
    work: the layout agent invents ids (``proj-ai``, ``card-helix-devops-infra``)
    that do not correspond to any project row, and cards carry no slug prop. So
    score the project rows directly.

    Same ``_score_blob`` the card reorder uses, so the tank's curation and the
    text view's card order stay one signal. Zero-score projects are excluded — a
    tank where every fish glows carries no curation.
    """
    if not projects or not tokens:
        return []

    scored: list[tuple[float, int, str]] = []
    for i, p in enumerate(projects):
        if not isinstance(p, dict):
            continue
        slug = str(p.get("slug") or "").strip()
        if not slug:
            continue
        score = _score_blob(_project_blob(p), tokens)
        if score > 0:
            scored.append((score, i, slug))

    # -score for best-first; index keeps ties in inventory order (stable).
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [slug for _, _, slug in scored[: max(0, _safe_int(limit, _MAX_HIGHLIGHT_SLUGS))]]


def rank_projects_for_tank(
    projects: list[dict] | None,
    tokens: set[str],
    *,
    limit: int,
    min_score: float = 0.0,
    always_slugs: list[str] | None = None,
) -> list[dict]:
    """Trim fish-tank to JD-relevant projects. Empty JD yields full inventory; always_slugs guarantees seats for highlights."""
    rows = [p for p in (projects or []) if isinstance(p, dict)]
    if not tokens or not rows:
        return rows

    always = {str(s).lower() for s in (always_slugs or []) if s}
    scored: list[tuple[float, int, dict]] = []
    for i, p in enumerate(rows):
        score = _score_blob(_project_blob(p), tokens)
        scored.append((score, i, p))

    scored.sort(key=lambda t: (-t[0], t[1]))

    kept: list[dict] = []
    max_limit = max(0, _safe_int(limit, 8))
    for score, _i, p in scored:
        slug_l = str(p.get("slug") or "").strip().lower()
        if score >= min_score or slug_l in always:
            kept.append(p)
        if len(kept) >= max_limit:
            break

    if not kept:
        logger.info("rank_projects_for_tank: no project cleared min_score, falling back to full inventory")
        return rows
    return kept


def content_fingerprint(layout: dict) -> str:
    """Cheap content fingerprint for cross-bake clone detection."""
    if not isinstance(layout, dict):
        return ""
    blocks = layout.get("blocks") if isinstance(layout.get("blocks"), list) else []
    hero = next((b for b in blocks if isinstance(b, dict) and b.get("type") == "hero"), None)
    pitch = ""
    if isinstance(hero, dict):
        props = hero.get("props") if isinstance(hero.get("props"), dict) else {}
        pitch = str(props.get("pitch") or "")
    cards = [
        str((b.get("props") or {}).get("title") or b.get("id") or "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "card"
    ]
    star = next((b for b in blocks if isinstance(b, dict) and b.get("type") == "starStory"), None)
    situation = ""
    if isinstance(star, dict):
        props = star.get("props") if isinstance(star.get("props"), dict) else {}
        situation = str(props.get("situation") or "")
    prose = next((b for b in blocks if isinstance(b, dict) and b.get("type") == "prose"), None)
    prose_open = ""
    if isinstance(prose, dict):
        props = prose.get("props") if isinstance(prose.get("props"), dict) else {}
        prose_open = str(props.get("markdown") or "")[:120]
    raw = "|".join([pitch, ",".join(cards), situation, prose_open])
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def tailor_layout_for_job(
    layout: dict | None,
    *,
    company: str,
    role: str,
    job_text: str = "",
    audience: str = "",
) -> dict | None:
    """Apply silent job matching without rewriting the hero.

    Pure / sync. Fail-open: returns input on bad shape. Never invents metrics.

    **Visible hero** (name / tagline / pitch / links) is left alone — the public
    ``?j=<short_id>`` and meta ``jobCompany`` / ``jobRole`` carry the target job.
    Differentiation is card reorder + optional neutral section titles + meta.
    """
    if not isinstance(layout, dict):
        return layout
    company_s = _norm(company)
    role_s = _norm(role)
    if not company_s and not role_s:
        return layout

    blocks_in = layout.get("blocks")
    if not isinstance(blocks_in, list):
        return layout

    job_text_s = _norm(job_text)
    tokens = _tokens(f"{role_s} {company_s} {job_text_s}")
    domain = _domain_cue(job_text_s, role_s)

    blocks: list[dict] = [dict(b) if isinstance(b, dict) else b for b in blocks_in]
    blocks = _reorder_cards_and_prose(
        [b for b in blocks if isinstance(b, dict)],
        tokens,
    )

    # --- Hero: DO NOT inject company/role (lives in ?j= + meta only) ---
    # Leave name / tagline / pitch / links byte-stable from compose.

    # --- Generic section titles: domain-only, never full job title ---
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        btype = str(b.get("type") or "")
        props = dict(b.get("props") or {}) if isinstance(b.get("props"), dict) else {}
        title = _norm(str(props.get("title") or ""))
        if btype == "flowAnim" and title.lower() in _GENERIC_FLOW_TITLES:
            props["title"] = (f"{domain} flow" if domain else "Systems flow")[:80]
            blocks[i] = {**b, "props": props}
        elif btype == "comparison" and title.lower() in _GENERIC_COMPARE_TITLES:
            props["title"] = (
                f"{domain} project contrast" if domain else "Project contrast"
            )[:80]
            blocks[i] = {**b, "props": props}

    # Project count for a neutral chrome label (no employer / role text)
    n_cards = sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "card")
    n_proj = n_cards or sum(
        1 for b in blocks if isinstance(b, dict) and b.get("type") == "projectGrid"
    )

    meta = dict(layout.get("meta") or {}) if isinstance(layout.get("meta"), dict) else {}
    # Silent job provenance for ?j= demos / resume PDF / analytics — not hero copy.
    if company_s:
        meta["jobCompany"] = company_s
    if role_s:
        meta["jobRole"] = role_s
    if audience:
        meta.setdefault("audience", audience)
    meta["jobBriefHash"] = _job_brief_hash(job_text_s or f"{company_s} {role_s}")
    meta["tailored"] = True
    # Visible chrome: short_id badge already shows demo · <id>; keep label neutral.
    if n_proj:
        meta["curationLabel"] = f"Matched layout · {n_proj} project(s)"
    else:
        meta["curationLabel"] = "Matched layout"

    # Note: meta.highlightSlugs is NOT set here. It must hold real project
    # slugs, and this function only sees blocks — whose ids the layout agent
    # invents. bake_tools stamps it from the project rows instead.

    out = {**layout, "blocks": blocks, "meta": meta}
    meta["contentFingerprint"] = content_fingerprint(out)
    out["meta"] = meta
    return out
