"""Portfolio content quality gates — demote stubs / sanitize card bodies.

Discovery write-back can flood ``portfolio_projects`` with thin GitHub stubs
(school labs, empty READMEs). Bake must treat those as weak context, not as
page material. Card bodies scraped from contribution markdown often start with
``Period Covered`` / table chrome / ASCII diagrams — reject those as display text.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from plugins.portfolio_plugin.schema.ui_layout_schema import UILink, UIStat

logger = logging.getLogger("whiskers.plugins.portfolio.quality")

# Thin discovery placeholders written by normalize when README is empty.
_GITHUB_STUB_RE = re.compile(
    r"^\s*#?\s*(?:GitHub repository\s+)?[\w.-]+/[\w.-]+\s*$",
    re.I,
)
_GITHUB_STUB_LINE_RE = re.compile(r"^\s*GitHub repository\s+\S+\s*$", re.I)
_VITE_TEMPLATE_RE = re.compile(
    r"This template provides a minimal setup to get React working",
    re.I,
)
_PERIOD_ONLY_RE = re.compile(
    r"^\s*\*?\*?Period Covered\*?\*?\s*:?\s*.{0,80}\s*$",
    re.I,
)
# Markdown table chrome or box-drawing art as "body"
_MARKDOWN_NOISE_RE = re.compile(
    r"(^\s*\|.+\|\s*$)|(^[│┌┐└┘├┤─═╔╗╚╝]+)|(^#+\s*$)",
    re.M,
)
# shields.io-style badge lines — "[![Unity 6](https://img.shields.io/...)](https://...)"
# or a bare "![alt](url)" image. READMEs opening with a badge wall (common on
# GitHub) would otherwise get treated as the first "real" line and concatenated
# into the summary/card body verbatim. A plain regex can't reliably match the
# "(url)" segment because badge query strings routinely contain their own
# parens (e.g. shields.io labels like "Agent-Model_Context_Protocol_(MCP)"),
# which a naive ``[^)]*`` stops at — so this is a small balanced-paren scanner
# instead of a single regex.


def _strip_md_image_links(s: str) -> str:
    """Remove every ``![alt](url)``, optionally ``[![alt](url)](url)``-wrapped,
    token from ``s``, tolerating one level of nested parens inside each url.
    Returns whatever text is left over (real prose, if any).
    """
    out: list[str] = []
    i, n = 0, len(s)

    def _consume_paren_group(pos: int) -> int:
        """``s[pos] == '('``; return index just past its matching ')'."""
        depth = 1
        j = pos + 1
        while j < n and depth > 0:
            if s[j] == "(":
                depth += 1
            elif s[j] == ")":
                depth -= 1
            j += 1
        return j

    while i < n:
        if s[i] == "!" and s[i + 1 : i + 2] == "[":
            close_bracket = s.find("]", i)
            if close_bracket != -1 and s[close_bracket + 1 : close_bracket + 2] == "(":
                end = _consume_paren_group(close_bracket + 1)
                # Optional outer link wrapper: ...](url)
                if s[end : end + 1] == "]" and s[end + 1 : end + 2] == "(":
                    end = _consume_paren_group(end + 1)
                i = end
                continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _is_badge_line(s: str) -> bool:
    """True when ``s`` is *only* markdown image/badge link syntax."""
    if not s.strip():
        return False
    remainder = _strip_md_image_links(s)
    return not re.sub(r"[\s\[\]()\-|]+", "", remainder)


def is_stub_summary(summary: str | None) -> bool:
    """True when summary is a discovery placeholder or empty template blurb."""
    s = (summary or "").strip()
    if not s:
        return True
    if len(s) < 48 and _GITHUB_STUB_RE.match(s):
        return True
    if _GITHUB_STUB_LINE_RE.fullmatch(s):
        return True
    if re.fullmatch(r"GitHub repository \S+", s, flags=re.I):
        return True
    if _VITE_TEMPLATE_RE.search(s) and len(s) < 220:
        return True
    # Owner/Repo only
    if re.fullmatch(r"[\w.-]+/[\w.-]+", s) and len(s) < 80:
        return True
    return False


def is_portfolio_worthy_project(project: dict[str, Any] | None) -> bool:
    """Whether a project row is rich enough to show on a job-bake page / tank.

    Primary-tagged rows always pass (hand-curated majors). Pure empty discovery
    stubs fail. **Active inventory rows that operators kept** (links and/or
    context_sources) pass even when the summary is still a thin GitHub
    placeholder — many hand-selected projects are not on Notion and never got
    a polished blurb, but they still belong in the fish tank and project list.
    Flood control is deactivation + discovery allowlist, not this gate.
    """
    if not isinstance(project, dict):
        return False
    tags = {str(t).strip().lower() for t in (project.get("tags") or []) if t}
    summary = str(project.get("summary") or "")
    metrics = project.get("metrics") if isinstance(project.get("metrics"), list) else []
    sources = (
        project.get("context_sources")
        if isinstance(project.get("context_sources"), list)
        else []
    )
    links = project.get("links") if isinstance(project.get("links"), list) else []
    has_metrics = bool(metrics)
    has_sources = bool(sources)
    has_links = any(isinstance(lnk, dict) and lnk.get("href") for lnk in links)

    if "primary" in tags:
        return True

    # Completely empty shell — nothing to render.
    if not summary.strip() and not has_metrics and not has_sources and not has_links:
        return False

    # Pure discovery placeholder with no operator attachment.
    if is_stub_summary(summary) and not has_metrics and not has_sources and not has_links:
        return False

    # Stub blurb but row is still intentional inventory (link / source / metrics).
    if is_stub_summary(summary):
        return has_metrics or has_sources or has_links

    if has_metrics or has_sources or has_links:
        return True
    # Manual insert with prose only (no github/notion yet) — short blurbs ok.
    if len(summary.strip()) < 40:
        return False
    return True


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def _strip_html_comments(text: str) -> str:
    """Drop complete ``<!--…-->`` blocks and any unterminated ``<!--`` tail.

    README chrome and leftover HTML comments, sliced at 600 chars, surface as
    a trailing ``<!`` on cards. Strip before any truncation so neither this
    module nor a raw client slice can keep the artifact.
    """
    s = _HTML_COMMENT_RE.sub("", text or "")
    cut = s.find("<!--")
    if cut >= 0:
        s = s[:cut]
    return s


def _truncate_at_boundary(text: str, max_len: int) -> str:
    """Cap ``text`` to ``max_len`` without cutting mid-word or mid-sentence.

    A raw ``text[:max_len]`` slice on a long-form project summary regularly
    lands mid-word ("...safely act on contact and account data, and auto")
    with no indication anything was cut. Prefer the last sentence boundary at
    or before the cap; fall back to the last word boundary; append an
    ellipsis whenever the text was actually shortened.
    """
    text = _strip_html_comments(text)
    if len(text) <= max_len:
        return text
    window = text[:max_len]
    sentence_ends = [i for i in (window.rfind("."), window.rfind("!"), window.rfind("?")) if i >= 0]
    last_sentence_end = max(sentence_ends) if sentence_ends else -1
    # Only trust a sentence boundary if it doesn't throw away most of the cap
    # (e.g. a single "e.g." early on shouldn't truncate a 600-char allowance
    # down to 40 chars).
    if last_sentence_end >= max_len * 0.4:
        return window[: last_sentence_end + 1]
    last_space = window.rfind(" ")
    if last_space > 0:
        return window[:last_space].rstrip(",;:—-") + "…"
    return window.rstrip() + "…"


def sanitize_card_body(text: str | None, *, min_len: int = 60) -> str | None:
    """Return display-safe card body or None if the text is chrome/noise."""
    raw = _strip_html_comments(text or "").strip()
    if not raw:
        return None
    if is_stub_summary(raw):
        return None
    if _PERIOD_ONLY_RE.match(raw):
        return None
    # Prefer first real paragraph — skip leading chrome lines. Blank lines are
    # paragraph separators, not stop signals: a README's H1 title (often just
    # the repo name again) or a badge wall commonly precede the first real
    # paragraph, each followed by its own blank line, so breaking on the first
    # blank line — before enough real content has accumulated — used to hand
    # the fallback regex below a chrome-only prefix to search through instead.
    lines = [ln.rstrip() for ln in raw.splitlines()]
    kept: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if _PERIOD_ONLY_RE.match(s) or re.match(r"(?i)^\*?\*?period covered\*?\*?", s):
            continue
        if s.startswith("|") and ("|" in s[1:]):
            continue
        if re.match(r"^[│┌┐└┘├┤─═╔╗╚╝\s\-\+]+$", s):
            continue
        if _is_badge_line(s):
            continue
        if re.match(r"^#+\s", s) and len(s) < 40:
            continue
        if re.match(r"^#{1,6}\s", s):
            s = re.sub(r"^#{1,6}\s*", "", s).strip()
        # A heading that is itself just "owner/repo" (GitHub's auto-generated
        # H1) or another stub-shaped line is chrome, not content — skip it
        # rather than seeding the body with the repo name a second time.
        if is_stub_summary(s):
            continue
        # Mid-document fragment (starts mid-sentence)
        if s and s[0].islower() and not kept:
            continue
        kept.append(s)
        if sum(len(x) for x in kept) >= min_len:
            if len(kept) >= 2 or s.endswith((".", "!", "?")):
                break
    body = " ".join(kept).strip()
    if len(body) < min_len:
        # Fall back: take first capital-started prose sentence from collapsed
        # *filtered* lines (badges/chrome/stub lines already dropped above) —
        # never the raw text, whose badge-markdown URLs contain "."
        # characters the sentence regex would otherwise stop at mid-URL.
        collapsed = re.sub(r"\s+", " ", " ".join(kept) or raw).strip()
        for m in re.finditer(r"(?s)([A-Z][^.]{40,500}\.)", collapsed):
            cand = m.group(1).strip()
            if (
                not is_stub_summary(cand)
                and "Period Covered" not in cand
                and cand.count("|") < 2
            ):
                return _truncate_at_boundary(cand, 600)
        return None
    if body[0].islower():
        return None
    if _MARKDOWN_NOISE_RE.search(body) and body.count("|") >= 2:
        body = re.sub(r"\|[^|]+\|", " ", body)
        body = re.sub(r"\s+", " ", body).strip()
        if len(body) < min_len:
            return None
    return _truncate_at_boundary(body, 600) if body else None


def filter_projects_for_layout(
    projects: list[dict[str, Any]] | None,
    *,
    keep_stubs_tagged_primary: bool = True,
) -> list[dict[str, Any]]:
    """Drop thin discovery stubs; keep curated / substantive rows."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in projects or []:
        if not isinstance(p, dict):
            continue
        slug = str(p.get("slug") or "").lower()
        if not slug or slug in seen:
            continue
        tags = {str(t).strip().lower() for t in (p.get("tags") or []) if t}
        if not is_portfolio_worthy_project(p):
            if not (keep_stubs_tagged_primary and "primary" in tags):
                continue
        seen.add(slug)
        out.append(p)
    return out


def project_metrics_and_links(proj: dict) -> tuple[list[UIStat], list[UILink]]:
    """Extract UIStat / UILink lists from a project dict (shared by grid/cards/fish)."""
    metrics: list[UIStat] = []
    raw_metrics = proj.get("metrics")
    if isinstance(raw_metrics, list):
        for m in raw_metrics:
            if isinstance(m, dict):
                label = m.get("label")
                value = m.get("value")
                if label is not None and value is not None:
                    metrics.append(UIStat(label=str(label), value=str(value)))

    links: list[UILink] = []
    raw_links = proj.get("links")
    if isinstance(raw_links, list):
        for l in raw_links:
            if isinstance(l, dict):
                label = l.get("label")
                href = l.get("href")
                if label is not None and href is not None:
                    if str(href).startswith("http://") or str(href).startswith("https://"):
                        links.append(UILink(label=str(label), href=str(href)))
                    else:
                        # Log only the offending href — never the raw link dict.
                        logger.warning(
                            "Dropping non-http(s) project link href: %s", str(href)[:200]
                        )
    return metrics, links


def is_stub_doc(doc: dict[str, Any] | None) -> bool:
    """True for thin github index rows that only restate the repo name."""
    if not isinstance(doc, dict):
        return True
    excerpt = str(doc.get("excerpt") or doc.get("content_text") or doc.get("content") or "")
    kind = str(doc.get("kind") or "").lower()
    if kind in ("case_study", "page", "url") and len(excerpt.strip()) >= 80:
        return False
    return is_stub_summary(excerpt) or (
        kind == "github" and len(excerpt.strip()) < 100 and "GitHub repository" in excerpt
    )


def filter_docs_for_context(
    docs: list[dict[str, Any]] | None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Prefer case studies / real pages; drop thin github stubs.

    When any rich docs exist, stubs are never mixed in (padding would re-
    introduce the noise we just filtered). Stubs only appear if *nothing*
    richer matched.
    """
    rich: list[dict[str, Any]] = []
    weak: list[dict[str, Any]] = []
    for d in docs or []:
        if not isinstance(d, dict):
            continue
        if is_stub_doc(d):
            weak.append(d)
        else:
            rich.append(d)
    out = list(rich) if rich else list(weak)
    if limit is not None:
        return out[:limit]
    return out


def dedupe_layout_cards(
    blocks: list[dict[str, Any]] | None,
    projects: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Drop later cards with the same normalized title (agent double-emit).

    When the agent stuffed contribution-markdown chrome into ``props.body``,
    sanitize it; if nothing usable remains, refill from a matching project
    summary when ``projects`` is provided.
    """
    if not isinstance(blocks, list):
        return []
    by_title: dict[str, dict[str, Any]] = {}
    by_slug: dict[str, dict[str, Any]] = {}
    for p in projects or []:
        if not isinstance(p, dict):
            continue
        name = re.sub(r"\s+", " ", str(p.get("name") or "").strip().lower())
        slug = str(p.get("slug") or "").lower()
        if name:
            by_title[name] = p
        if slug:
            by_slug[slug] = p

    seen_titles: set[str] = set()
    seen_slugs: set[str] = set()
    out: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if str(b.get("type") or "") != "card":
            out.append(b)
            continue
        props = b.get("props") if isinstance(b.get("props"), dict) else {}
        title = re.sub(r"\s+", " ", str(props.get("title") or "").strip().lower())
        bid = str(b.get("id") or "").lower()
        slug_hint = bid.replace("card-", "").replace("proj-", "")
        if title and title in seen_titles:
            continue
        if slug_hint and slug_hint in seen_slugs:
            continue
        if title:
            seen_titles.add(title)
        if slug_hint:
            seen_slugs.add(slug_hint)
        body = sanitize_card_body(props.get("body") if isinstance(props, dict) else None)
        if body is None:
            # Agent body was chrome / mid-doc junk — author display copy from
            # inventory context (never raw summary paste).
            src = by_slug.get(slug_hint) or by_title.get(title)
            if src:
                from plugins.portfolio_plugin.compose.display_copy import (
                    author_display_copy,
                )

                body = author_display_copy(src, form="card_body")
        if body is not None and isinstance(props, dict):
            b = {**b, "props": {**props, "body": body}}
        elif isinstance(props, dict) and props.get("body"):
            # Drop garbage body rather than ship chrome
            new_props = {k: v for k, v in props.items() if k != "body"}
            b = {**b, "props": new_props}
        out.append(b)
    return out
