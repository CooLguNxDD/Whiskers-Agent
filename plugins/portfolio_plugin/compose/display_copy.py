"""Author visitor-facing project copy from inventory context (zero-paste).

``portfolio_projects.summary`` is planner/inventory seed material — not page
output. Floor cards, fish blurbs, and timeline bodies must reframe that seed
(plus tags/metrics/name and optional layout-authored body) into display strings
that are never a raw ``summary[:N]`` dump.

Char caps come from ``manifest.json`` → ``settings.display_copy`` (see
``get_display_copy_caps``).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

logger = logging.getLogger("whiskers.plugins.portfolio")

Form = Literal["fish_blurb", "card_body", "description"]

# Defaults when manifest omits display_copy (also the documented baseline).
_DEFAULT_CAPS: dict[Form, int] = {
    "fish_blurb": 200,
    "card_body": 480,
    "description": 600,
}
_CAP_RANGES: dict[Form, tuple[int, int]] = {
    "fish_blurb": (80, 400),
    "card_body": (200, 1200),
    "description": (200, 1200),
}

_SENTENCE_RE = re.compile(r"(?s)([A-Z][^.!?]*[.!?])")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+.#-]{1,32}", re.I)
# Agent paste of inventory summary often wraps the lead in markdown chrome.
_MD_LEAD_RE = re.compile(r"^\s*>?\s*\*{0,2}")


def get_display_copy_caps() -> dict[Form, int]:
    """Return form → max chars from plugin SETTINGS.display_copy.

    Missing/invalid keys fall back to defaults; values clamped to safe ranges
    so a typo cannot blow GenUI tooltips.
    """
    raw: dict[str, Any] = {}
    try:
        from plugins.portfolio_plugin.plugin_config import SETTINGS

        dc = SETTINGS.get("display_copy") if isinstance(SETTINGS, dict) else None
        if isinstance(dc, dict):
            raw = dc
    except Exception:
        raw = {}

    key_map = {
        "fish_blurb": "fish_blurb_max_chars",
        "card_body": "card_body_max_chars",
        "description": "description_max_chars",
    }
    out: dict[Form, int] = {}
    for form, key in key_map.items():
        lo, hi = _CAP_RANGES[form]
        default = _DEFAULT_CAPS[form]
        try:
            v = int(raw.get(key, default))
        except (TypeError, ValueError):
            v = default
        out[form] = max(lo, min(hi, v))  # type: ignore[assignment]
    return out  # type: ignore[return-value]


def _form_cap(form: Form) -> int:
    return get_display_copy_caps().get(form, _DEFAULT_CAPS[form])


def _norm(s: str) -> str:
    # Strip common markdown chrome so dump detection still matches inventory.
    t = (s or "").strip()
    t = re.sub(r"^>\s*", "", t)
    t = re.sub(r"\*{1,2}", "", t)
    t = re.sub(r"`+", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "") if len(m.group(0)) > 2}


def _score_text(text: str, job_tokens: set[str] | None) -> float:
    if not job_tokens or not text:
        return 0.0
    low = text.lower()
    return float(sum(1 for t in job_tokens if t in low))


def _sentences_from(text: str) -> list[str]:
    """Split prose into claim sentences; drop trivial fragments."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return []
    found = [m.group(1).strip() for m in _SENTENCE_RE.finditer(raw)]
    if found:
        return [s for s in found if len(s) >= 24]
    if len(raw) >= 24:
        return [raw]
    return []


def _cue_from_project(
    project: dict[str, Any],
    *,
    job_tokens: set[str] | None = None,
) -> str:
    """Short domain/tag cue for the reframe lead-in."""
    tags = project.get("tags") if isinstance(project.get("tags"), list) else []
    tag_strs = [str(t).strip() for t in tags if t and str(t).strip().lower() != "primary"]
    if job_tokens:
        hits = [t for t in tag_strs if any(tok in t.lower() for tok in job_tokens)]
        if hits:
            return ", ".join(hits[:3])
    if tag_strs:
        return ", ".join(tag_strs[:3])
    try:
        from plugins.portfolio_plugin.compose.ranking import _infer_domain

        domain = _infer_domain(project)
        if domain:
            return domain
    except Exception:
        logger.debug("_infer_domain failed for project=%r", project.get("slug"), exc_info=True)
    return ""


def _metric_chip(project: dict[str, Any]) -> str:
    metrics = project.get("metrics") if isinstance(project.get("metrics"), list) else []
    chips: list[str] = []
    for m in metrics[:2]:
        if not isinstance(m, dict):
            continue
        label = str(m.get("label") or "").strip()
        value = str(m.get("value") or "").strip()
        if label and value:
            chips.append(f"{label}: {value}")
        elif value:
            chips.append(value)
    return " · ".join(chips)


def _is_prefix_dump(authored: str, source: str, *, cap: int) -> bool:
    """True when authored is just a truncated prefix of the inventory seed."""
    a = _norm(authored)
    s = _norm(source)
    if not a or not s or len(a) < 40:
        return False
    if a == s:
        return True
    if len(s) > cap and (s.startswith(a) or a == s[: len(a)]):
        return True
    from plugins.portfolio_plugin.compose.quality import _truncate_at_boundary

    truncated = _norm(_truncate_at_boundary(source, cap))
    return bool(truncated) and a == truncated


def is_inventory_dump(text: str | None, project: dict[str, Any] | None) -> bool:
    """True when ``text`` is (or is chrome around) the inventory summary seed.

    Layout agents often paste ``portfolio_projects.summary`` into card bodies,
    sometimes wrapped in ``> **…**`` markdown. Those must be re-authored, not
    treated as visitor-facing display copy.
    """
    if not text or not isinstance(project, dict):
        return False
    summary = str(project.get("summary") or "").strip()
    if not summary or len(summary) < 40:
        return False
    a = _norm(text)
    s = _norm(summary)
    if not a or len(a) < 40:
        return False
    if a == s:
        return True
    # Prefix dump (agent or sanitize truncated the same seed).
    if s.startswith(a) and len(a) >= min(80, len(s) * 0.5):
        return True
    if a.startswith(s) and len(s) >= 80:
        return True
    # High token overlap with summary lead (markdown chrome stripped already).
    lead = s[: min(160, len(s))]
    if lead and lead in a:
        return True
    # Markdown-wrapped inventory lead: "> **Multiplayer Co-op..."
    stripped = _MD_LEAD_RE.sub("", (text or "").strip())
    stripped_n = _norm(stripped)
    if stripped_n and (stripped_n == s or s.startswith(stripped_n[:120])):
        return True
    # Double-star title chrome common on agent paste of GitHub H1 + body.
    if "**" in (text or "") and lead[:60] in a:
        return True
    return False


def _pick_claims(
    sentences: list[str],
    *,
    job_tokens: set[str] | None,
    count: int,
    skip: set[str] | None = None,
) -> list[str]:
    if not sentences:
        return []
    skip_n = {_norm(x) for x in (skip or set()) if x}
    ranked = sorted(
        enumerate(sentences),
        key=lambda it: (-_score_text(it[1], job_tokens), it[0]),
    )
    out: list[str] = []
    for _, s in ranked:
        if _norm(s) in skip_n:
            continue
        out.append(s)
        if len(out) >= count:
            break
    if not out:
        for _, s in ranked[:count]:
            out.append(s)
    return out


def _reframe(
    *,
    name: str,
    cue: str,
    claims: list[str],
    metric_chip: str,
    form: Form,
    source_for_guard: str,
) -> str | None:
    """Build structured display text; never a naked summary slice."""
    from plugins.portfolio_plugin.compose.quality import _truncate_at_boundary

    cap = _form_cap(form)
    lead = claims[0] if claims else ""
    if lead and _norm(lead).startswith(_norm(name)) and len(claims) > 1:
        lead = claims[1]
        claims = claims[1:]

    if form == "fish_blurb":
        parts: list[str] = []
        if name and cue:
            parts.append(f"{name} — {cue}")
        elif name:
            parts.append(name)
        if lead:
            if name and _norm(name) in _norm(lead):
                # Keep structured head when we have cue; else claim alone.
                if cue:
                    parts = [f"{name} — {cue}", lead]
                else:
                    parts = [lead]
            else:
                parts.append(lead)
        text = ". ".join(p.rstrip(".") for p in parts if p).strip()
        if len(text) > cap:
            short = (f"{name} — {cue}" if name and cue else None) or lead or name
            text = short
    elif form == "card_body":
        head = f"{name} — {cue}." if name and cue else (f"{name}." if name else "")
        body_claims = claims[:2] if claims else []
        chunks: list[str] = []
        if head:
            chunks.append(head.rstrip("."))
        for c in body_claims:
            if head and _norm(c).startswith(_norm(name)) and len(body_claims) > 1:
                continue
            chunks.append(c.rstrip("."))
            break
        if len(chunks) <= (1 if head else 0) and body_claims:
            chunks.append(body_claims[0].rstrip("."))
        if metric_chip:
            chunks.append(metric_chip)
        if not chunks and name:
            chunks = [f"{name}" + (f" — {cue}" if cue else "")]
        text = ". ".join(chunks).strip()
        if text and not text.endswith((".", "!", "?", "…")):
            text += "."
    else:  # description
        head = f"{name} — {cue}." if name and cue else ""
        body_claims = claims[:2] if claims else []
        parts: list[str] = []
        if head:
            parts.append(head.rstrip("."))
        for c in body_claims:
            parts.append(c.rstrip("."))
        if not parts and name:
            parts = [f"{name}" + (f" — {cue}" if cue else "")]
        text = ". ".join(parts).strip()
        if text and not text.endswith((".", "!", "?", "…")):
            text += "."
        if metric_chip and metric_chip not in text:
            text = f"{text} ({metric_chip})" if text else metric_chip

    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    text = _truncate_at_boundary(text, cap)

    if source_for_guard and _is_prefix_dump(text, source_for_guard, cap=cap):
        fallback = f"{name} — {cue}" if name and cue else (name or cue or "")
        fallback = _truncate_at_boundary(fallback.strip(), cap)
        if fallback and not _is_prefix_dump(fallback, source_for_guard, cap=cap):
            return fallback
        return _truncate_at_boundary(name, cap) if name else None
    return text or None


def author_display_copy(
    project: dict[str, Any] | None,
    *,
    form: Form = "card_body",
    job_tokens: set[str] | None = None,
    preferred_body: str | None = None,
) -> str | None:
    """Author visitor-facing copy from inventory context.

    Never returns a raw ``summary[:N]`` dump when a structured reframe is
    possible. Layout-authored ``preferred_body`` is used only when it is **not**
    an inventory dump; dumps are re-authored from seed + tags/metrics.
    """
    if not isinstance(project, dict):
        return None

    from plugins.portfolio_plugin.compose.quality import (
        is_stub_summary,
        sanitize_card_body,
        _truncate_at_boundary,
    )

    name = str(project.get("name") or project.get("slug") or "").strip()
    summary = str(project.get("summary") or "").strip()
    cap = _form_cap(form)
    cue = _cue_from_project(project, job_tokens=job_tokens)
    metric_chip = _metric_chip(project)

    # 1) Preferred body only when it is not a dump of inventory summary.
    pref = (preferred_body or "").strip()
    if pref and is_inventory_dump(pref, project):
        pref = ""
    if pref:
        cleaned = sanitize_card_body(pref, min_len=24) or (
            pref if len(pref) >= 24 and not is_stub_summary(pref) else None
        )
        if cleaned and is_inventory_dump(cleaned, project):
            cleaned = None
        if cleaned:
            if form == "fish_blurb":
                sents = _sentences_from(cleaned)
                claims = _pick_claims(sents, job_tokens=job_tokens, count=1)
                return _reframe(
                    name=name,
                    cue=cue,
                    claims=claims or [cleaned],
                    metric_chip="",
                    form="fish_blurb",
                    source_for_guard=summary or cleaned,
                )
            # Card body: keep distinct agent prose only when already reframed
            # or clearly not a summary paste; else re-author below.
            if form == "card_body":
                if " — " in cleaned or not is_inventory_dump(cleaned, project):
                    # Still reject near-prefix of summary after sanitize.
                    if not _is_prefix_dump(cleaned, summary, cap=cap):
                        return _truncate_at_boundary(cleaned, cap)
            else:
                return _truncate_at_boundary(cleaned, cap)

    # 2) Ingredients from inventory seed (sanitize only — never ship raw).
    seed = sanitize_card_body(summary, min_len=24) if summary else None
    if not seed and is_stub_summary(summary):
        seed = None
    sentences = _sentences_from(seed or "")
    if not sentences and summary and not is_stub_summary(summary) and len(summary) >= 40:
        sentences = _sentences_from(summary)

    claim_count = 1 if form == "fish_blurb" else (2 if form == "card_body" else 3)
    claims = _pick_claims(sentences, job_tokens=job_tokens, count=claim_count)

    if not claims and not name and not cue and not metric_chip:
        return None

    if not claims and is_stub_summary(summary) and not cue and not metric_chip:
        return None

    if not claims:
        if form == "fish_blurb":
            tagline = f"{name} — {cue}" if name and cue else (name or cue)
            return _truncate_at_boundary(tagline, cap) if tagline else None
        if metric_chip and name:
            return _truncate_at_boundary(f"{name} — {metric_chip}", cap)
        return _truncate_at_boundary(name or cue or "", cap) or None

    return _reframe(
        name=name,
        cue=cue,
        claims=claims,
        metric_chip=metric_chip if form != "fish_blurb" else "",
        form=form,
        source_for_guard=summary,
    )


def body_by_slug_from_layout(layout: dict[str, Any] | None) -> dict[str, str]:
    """Map project slug → card body from a composed layout."""
    if not isinstance(layout, dict):
        return {}
    blocks = layout.get("blocks")
    if not isinstance(blocks, list):
        return {}
    out: dict[str, str] = {}
    for b in blocks:
        if not isinstance(b, dict) or str(b.get("type") or "") != "card":
            continue
        props = b.get("props") if isinstance(b.get("props"), dict) else {}
        body = str(props.get("body") or "").strip()
        if not body:
            continue
        bid = str(b.get("id") or "").lower()
        slug = ""
        if bid.startswith("card-"):
            slug = bid[5:]
        elif bid.startswith("proj-"):
            slug = bid[5:]
        if props.get("slug"):
            slug = str(props["slug"]).strip().lower()
        if slug:
            out[slug] = body
    return out


def _index_projects(
    projects: list[dict[str, Any]] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_slug: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    for p in projects or []:
        if not isinstance(p, dict):
            continue
        slug = str(p.get("slug") or "").strip().lower()
        name = re.sub(r"\s+", " ", str(p.get("name") or "").strip().lower())
        if slug:
            by_slug[slug] = p
        if name:
            by_title[name] = p
    return by_slug, by_title


def _card_slug(block: dict[str, Any]) -> str:
    bid = str(block.get("id") or "").lower()
    props = block.get("props") if isinstance(block.get("props"), dict) else {}
    if props.get("slug"):
        return str(props["slug"]).strip().lower()
    if bid.startswith("card-"):
        return bid[5:]
    if bid.startswith("proj-"):
        return bid[5:]
    return ""


def rewrite_layout_project_copy(
    layout: dict[str, Any] | None,
    projects: list[dict[str, Any]] | None,
    *,
    job_tokens: set[str] | None = None,
    display_copy_by_slug: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Re-author card bodies that dump inventory; return (layout, body_by_slug).

    Optional ``display_copy_by_slug`` (from FlowSpec author_display stage) seeds
    card bodies with precomputed ``card_body`` strings when present and not dumps.
    """
    if not isinstance(layout, dict):
        return layout, {}
    blocks_in = layout.get("blocks")
    if not isinstance(blocks_in, list):
        return layout, {}

    by_slug, by_title = _index_projects(projects)
    pre: dict[str, str] = {}
    if isinstance(display_copy_by_slug, dict):
        for k, v in display_copy_by_slug.items():
            slug = str(k).strip().lower()
            if not slug:
                continue
            if isinstance(v, dict) and v.get("card_body"):
                pre[slug] = str(v["card_body"])
            elif isinstance(v, str) and v.strip():
                pre[slug] = v.strip()

    new_blocks: list[Any] = []
    body_by_slug: dict[str, str] = {}

    for b in blocks_in:
        if not isinstance(b, dict) or str(b.get("type") or "") != "card":
            new_blocks.append(b)
            continue
        props = dict(b.get("props") if isinstance(b.get("props"), dict) else {})
        slug = _card_slug(b)
        title = re.sub(r"\s+", " ", str(props.get("title") or "").strip().lower())
        proj = by_slug.get(slug) or by_title.get(title)
        old_body = str(props.get("body") or "").strip()
        authored: str | None = None
        if slug and slug in pre:
            candidate = pre[slug]
            if not proj or not is_inventory_dump(candidate, proj):
                authored = candidate
        if authored is None and proj is not None:
            if not old_body or is_inventory_dump(old_body, proj):
                authored = author_display_copy(
                    proj, form="card_body", job_tokens=job_tokens
                )
            else:
                # Distinct agent prose — keep, still dump-check after soft clean.
                authored = author_display_copy(
                    proj,
                    form="card_body",
                    job_tokens=job_tokens,
                    preferred_body=old_body,
                )
        elif authored is None and old_body:
            authored = old_body

        if authored:
            props["body"] = authored
            if slug:
                body_by_slug[slug] = authored
            new_blocks.append({**b, "props": props})
        else:
            new_blocks.append(b)

    return {**layout, "blocks": new_blocks}, body_by_slug


def build_display_copy_by_slug(
    projects: list[dict[str, Any]] | None,
    *,
    job_tokens: set[str] | None = None,
) -> dict[str, dict[str, str]]:
    """Author dual-granularity blobs for each project (FlowSpec / MCP op)."""
    out: dict[str, dict[str, str]] = {}
    for p in projects or []:
        if not isinstance(p, dict):
            continue
        slug = str(p.get("slug") or "").strip().lower()
        if not slug:
            continue
        blob: dict[str, str] = {}
        for form in ("fish_blurb", "card_body", "description"):
            text = author_display_copy(p, form=form, job_tokens=job_tokens)  # type: ignore[arg-type]
            if text:
                blob[form] = text
        if blob:
            out[slug] = blob
    return out
