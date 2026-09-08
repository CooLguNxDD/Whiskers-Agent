"""Project ranking for portfolio layout composition.

Extracted from ``composer.py`` so lexical/semantic ranking and inventory
priors stay greppable without behavior change.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("whiskers.plugins.portfolio.compose.ranking")

_QUERY_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.]{1,}", re.I)
# Tiny stop list — keep agentic terms (agent, mcp, api) out of this set.
_QUERY_STOP = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "your", "our", "are",
    "was", "were", "will", "not", "you", "into", "have", "has", "had", "but",
    "or", "an", "of", "to", "in", "on", "at", "by", "as", "is", "be", "we",
    "they", "their", "them", "job", "role", "remote", "senior", "staff",
    "years", "experience", "looking", "about", "more", "than", "over",
})


def _query_tokens(query: str) -> set[str]:
    toks = {t.lower() for t in _QUERY_TOKEN_RE.findall(query or "")}
    return {t for t in toks if t not in _QUERY_STOP and len(t) >= 2}


# High-signal agentic/AI tokens get extra weight so thin GitHub rows can
# compete with dense employment prose in the semantic index.
_AGENTIC_BOOST_TOKENS = frozenset({
    "agent", "agentic", "agents", "mcp", "langgraph", "langchain", "llm",
    "llms", "rag", "orchestration", "orchestrator", "goap", "oauth",
    "tool-calling", "tools", "genui", "openai", "vector", "embedding",
    "embeddings", "multi-agent", "autonomous",
})


def _infer_domain(proj: dict) -> str | None:
    """Best-effort Open Design domain tint from slug/tags/name."""
    tags = proj.get("tags") if isinstance(proj.get("tags"), list) else []
    hay = f"{proj.get('slug') or ''} {proj.get('name') or ''} {' '.join(str(t) for t in tags)}".lower()
    if re.search(r"mcp|langgraph|pgvector|ai|llm|agent|openai|operator", hay):
        return "ai"
    if re.search(r"aws|terraform|eks|devops|infra|docker|k8s|runner", hay):
        return "devops"
    if re.search(r"mobile|react.?native|amplify|graphql", hay):
        return "mobile"
    if re.search(r"sms|platform|axios|messaging|portal", hay):
        return "platform"
    return None


def _project_lexical_score(project: dict, tokens: set[str]) -> float:
    """Score a project row against JD tokens via name/summary/tags/slug/refs.

    Semantic index hits alone bias toward employment rows with dense prose;
    discovered GitHub projects often only have thin README stubs in the
    index, so lexical overlap on tags (MCP, LangGraph, agentic, …) is the
    bridge that lets them surface in job-bake top_k cards.
    """
    if not tokens or not isinstance(project, dict):
        return 0.0
    slug = str(project.get("slug") or "").lower()
    name = str(project.get("name") or "").lower()
    summary = str(project.get("summary") or "").lower()
    tags = " ".join(str(t).lower() for t in (project.get("tags") or []) if t)
    refs = " ".join(
        str(src.get("ref") or "").lower()
        for src in (project.get("context_sources") or [])
        if isinstance(src, dict)
    )
    # Weighted fields: tags/name matter most for thin discovery rows.
    bag = (
        f"{slug} {slug.replace('-', ' ')} "
        f"{name} {name} "
        f"{tags} {tags} {tags} "
        f"{summary} "
        f"{refs}"
    )
    if not bag.strip():
        return 0.0
    words = set(bag.split())
    hits = 0.0
    for tok in tokens:
        weight = 1.75 if tok in _AGENTIC_BOOST_TOKENS else 1.0
        if tok in bag:
            hits += weight
            continue
        # Soft stem: "agent" ↔ "agentic" / "agents"
        if len(tok) >= 4 and any(tok in w or w.startswith(tok) for w in words):
            hits += 0.65 * weight
    if hits <= 0:
        return 0.0
    # GitHub-backed rows get a small floor when any agentic tag matches —
    # discovery write-backs are the inventory the user expects to see.
    has_github = any(
        isinstance(src, dict) and str(src.get("kind") or "").lower() == "github"
        for src in (project.get("context_sources") or [])
    )
    floor = 0.12 if has_github and hits >= 1.0 else 0.0
    # Normalize by query size so long JDs don't drown short tag hits.
    return min(1.15, floor + hits / max(3.5, len(tokens) * 0.28))


def _project_family(project: dict) -> str:
    """Coarse family key for diversity — prefer domain over employer tags."""
    try:
        domain = _infer_domain(project)
        if domain:
            return str(domain).lower()
    except Exception as exc:
        logger.debug("project_family domain infer skipped: %s", exc, exc_info=True)
    tags = project.get("tags") if isinstance(project.get("tags"), list) else []
    domain_tags = {"ai", "devops", "mobile", "platform", "infra", "frontend", "backend"}
    for t in tags:
        s = str(t or "").strip().lower()
        if s in domain_tags:
            return s
    slug = str(project.get("slug") or "").lower()
    if "-" in slug:
        return slug.split("-", 1)[0]
    return slug or "_"


def _content_depth(project: dict) -> float:
    """Continuous content-depth score from summary/metrics/context_sources."""
    if not isinstance(project, dict):
        return 0.0
    summary = str(project.get("summary") or "").strip()
    metrics = project.get("metrics") if isinstance(project.get("metrics"), list) else []
    sources = project.get("context_sources") if isinstance(project.get("context_sources"), list) else []
    depth = 0.0
    depth += min(0.35, len(summary) / 800.0)
    depth += min(0.25, 0.05 * len(metrics))
    depth += min(0.25, 0.06 * len(sources))
    if re.fullmatch(r"GitHub repository \S+", summary):
        depth -= 0.45
    try:
        from plugins.portfolio_plugin.compose.quality import is_stub_summary

        if is_stub_summary(summary):
            depth -= 0.55
    except Exception as exc:
        logger.debug("content_depth stub check skipped: %s", exc, exc_info=True)
    return depth


def _priority_bias(project: dict) -> float:
    """Authored portfolio prior: primary OSS platforms up, side bots down.

    ``sort_order`` is the human-curated rank (1 = top). Tags ``primary`` /
    ``side`` are explicit inventory signals from discovery write-back or
    hand curation — Pullfrog-style custom builds should not outrank the
    Whiskers Agent platform on agentic job bakes.
    """
    if not isinstance(project, dict):
        return 0.0
    tags = {str(t).strip().lower() for t in (project.get("tags") or []) if t}
    slug = str(project.get("slug") or "").lower()
    name = str(project.get("name") or "").lower()
    bias = 0.0

    # Explicit inventory flags
    if "primary" in tags:
        bias += 0.55
    if "side" in tags:
        bias -= 0.70

    # Content depth prior (summary/metrics/context_sources); includes stub demotion.
    bias += _content_depth(project)

    # Soft prior from curated sort_order (0 first … large last).
    # Do not use ``or 50`` — sort_order 0 is a valid top rank and is falsy.
    try:
        raw_so = project.get("sort_order")
        so = 50 if raw_so is None else int(raw_so)
    except (TypeError, ValueError):
        so = 50
    bias += max(0.0, 0.32 - so * 0.011)

    return bias


async def rank_projects_by_query(
    projects: list[dict],
    query: str | None,
    *,
    tenant_id: int = 1,
    top_k: int = 12,
) -> list[dict]:
    """Reorder projects by semantic hits + lexical row signals.

    Semantic search on ``portfolio_plugin__context`` is primary. Lexical
    overlap on name/summary/tags/slug is always merged so discovered GitHub
    projects (often thin in the index) still rank when the JD matches their
    tags (MCP, agentic, LangGraph, …). When scores are empty, input order
    is preserved. A light family-diversity pass spreads top slots so one
    employer does not fill all ``top_k`` cards.
    """
    if not projects or not query or not str(query).strip():
        return projects

    q = str(query)
    tokens = _query_tokens(q)
    scores: dict[str, float] = {}

    # Lexical baseline from the project row itself (always).
    for p in projects:
        slug = str(p.get("slug") or "").lower()
        if not slug:
            continue
        lex = _project_lexical_score(p, tokens)
        if lex > 0:
            scores[slug] = max(scores.get(slug, 0.0), lex)

    # Semantic hits from the context index (boost / override).
    hits: list = []
    try:
        from plugins.portfolio_plugin.discovery.index import search_context

        hits = await search_context(q, tenant_id=tenant_id, top_k=max(int(top_k), 24))
    except Exception as exc:
        logger.debug("rank_projects_by_query search fail-open: %s", exc, exc_info=True)
        hits = []

    if hits:
        from plugins.portfolio_plugin.discovery.slug import keys_match

        for i, h in enumerate(hits):
            meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
            hint = str(meta.get("slug_hint") or "")
            ref = str(meta.get("ref") or "")
            title = str(meta.get("title") or "")
            sim = float(h.get("similarity") or 0.0)
            # Earlier hits get a small rank boost if similarity missing
            score = sim if sim else (1.0 / (i + 1))
            for p in projects:
                slug = str(p.get("slug") or "")
                if not slug:
                    continue
                # Slug-format mismatch (hyphen vs underscore) can't bridge on the
                # slug alone (e.g. "oct" vs "opencat-mcp-full") — also match the
                # project's own declared context_sources/links, which is the only
                # thing that reliably ties a hand-authored slug to a discovered ref.
                href_candidates = [
                    str(src.get("ref") or "") for src in (p.get("context_sources") or []) if isinstance(src, dict)
                ] + [
                    str(link.get("href") or "") for link in (p.get("links") or []) if isinstance(link, dict)
                ]
                if keys_match(slug, hint, ref, title) or any(keys_match(c, ref) for c in href_candidates if c):
                    # Sum support across hits (depth), not max-only.
                    import math
                    key = slug.lower()
                    prev = scores.get(key, 0.0)
                    n_hits = getattr(rank_projects_by_query, "_hit_counts", None)
                    if not isinstance(n_hits, dict):
                        rank_projects_by_query._hit_counts = {}
                        n_hits = rank_projects_by_query._hit_counts
                    n_hits[key] = n_hits.get(key, 0) + 1
                    n = n_hits[key]
                    combined = max(prev, score) + 0.20 * math.log1p(max(0, n - 1))
                    scores[key] = min(combined + 0.15 * prev, prev + score + 1.0)

    # Curated prior: Whiskers Agent primary platform up, Pullfrog/side bots down.
    # Applied even when semantic/lexical left a slug at 0 so demotions still
    # reorder relative to peers that only have weak index hits.
    # When the JD strongly matches a non-primary project, dampen primary bias
    # so inventory prior cannot pin every job bake to the same card order.
    max_lex = max(scores.values()) if scores else 0.0
    dampen_primary = max_lex >= 2.0
    for p in projects:
        slug = str(p.get("slug") or "").lower()
        if not slug:
            continue
        bias = _priority_bias(p)
        if dampen_primary and bias > 0:
            bias *= 0.5
        if bias != 0.0 or slug in scores:
            scores[slug] = scores.get(slug, 0.0) + bias

    if not scores:
        return projects

    # Primary sort by score, then sort_order.
    ordered = sorted(
        projects,
        key=lambda p: (
            -scores.get(str(p.get("slug") or "").lower(), 0.0),
            int(p.get("sort_order") or 0),
            str(p.get("slug") or "").lower(),
        ),
    )

    # Diversity: greedy re-pick so one family (e.g. Helix) cannot monopolize
    # the head of the list when other families have non-zero scores.
    picked: list[dict] = []
    remaining = list(ordered)
    family_count: dict[str, int] = {}
    while remaining:
        best_i = 0
        best_adj = float("-inf")
        for i, p in enumerate(remaining):
            slug = str(p.get("slug") or "").lower()
            base = scores.get(slug, 0.0)
            fam = _project_family(p)
            # Progressive penalty so one employer cannot fill all top_k cards
            # when other families have non-zero scores (common Helix mono-fill).
            n_fam = family_count.get(fam, 0)
            adj = base - (0.28 * n_fam + 0.18 * max(0, n_fam - 1))
            if adj > best_adj:
                best_adj = adj
                best_i = i
        chosen = remaining.pop(best_i)
        picked.append(chosen)
        fam = _project_family(chosen)
        family_count[fam] = family_count.get(fam, 0) + 1

    return picked
