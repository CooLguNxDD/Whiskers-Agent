"""Evidence pack for context-first layout planning.

``portfolio_projects`` and ``portfolio_plugin__context`` are **planner
context only** — never the bake/layout tool return payload and never a
page skeleton to dump. Retrieve once, budget into the planner prompt,
then let the LLM assemble GenUI blocks from the FE catalog.

The stale ``portfolio_star`` collection is not used.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.evidence")


def block_catalog_entries() -> list[dict[str, object]]:
    """Return full ``BlockFeature`` catalog entries (band/grounding/props/hints).

    Delegates to ``schema.block_catalog`` — the single source of truth for
    per-type picking context, derived from the real dispatch maps so it can't
    drift from what materialization actually does.
    """
    from plugins.portfolio_plugin.schema.block_catalog import (
        block_feature_dict,
        get_block_catalog,
    )

    return [block_feature_dict(f) for f in get_block_catalog().values()]


def format_block_catalog(max_chars: int = 4500) -> str:
    """Budgeted catalog text with band/grounding/props/hints for the planner prompt."""
    lines = [
        "Available GenUI block types (portfolio_plugin schema / CatPortfolio — only these):",
        "FREEDOM: pick any mix that serves the brief; invent structure from catalog + evidence.",
        "PLANNING METHOD: brainstorm ~14–16 candidates, ship 10–14 high-value blocks.",
        "RHYTHM: project cards → layout.span=6 and band cols=2 (two cards per row).",
        "Deep dive prose/composite/code → span=12. Prefer multi-project coverage.",
        "Fields below: band (matrix L-level) · grounding (db=server-derived, "
        "authored=you supply props+source_refs, widget=zero-cost client interactive, "
        "dual=either) · span (default grid width) · renders · when · avoid.",
    ]
    try:
        entries = block_catalog_entries()
    except Exception:
        entries = []
    for e in entries:
        band = e.get("band") if isinstance(e, dict) else None
        band_s = f"L{band.get('level')} {band.get('label')}" if isinstance(band, dict) else ""
        line = (
            f"- {e.get('type')} [{band_s} · {e.get('grounding')} · span={e.get('default_span')}] "
            f"props={e.get('props_signature')} — {e.get('renders')} "
            f"WHEN: {e.get('when_to_use')}"
        )
        avoid = e.get("avoid_when")
        if avoid:
            line += f" AVOID: {avoid}"
        lines.append(line)
    text = "\n".join(lines)
    if len(text) > max_chars:
        # Trim avoid-clauses first (least critical) before hard truncation.
        trimmed = [re.sub(r" AVOID:.*$", "", ln) for ln in lines]
        text2 = "\n".join(trimmed)
        if len(text2) <= max_chars:
            return text2
        return text2[: max_chars - 1].rstrip() + "…"
    return text


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: max(1, n - 1)].rstrip() + "…"


def _project_row(p: dict[str, Any], *, summary_chars: int = 480) -> dict[str, Any]:
    metrics = p.get("metrics") if isinstance(p.get("metrics"), list) else []
    slim_metrics = []
    for m in metrics[:6]:
        if isinstance(m, dict) and m.get("label") is not None:
            slim_metrics.append(
                {"label": str(m.get("label")), "value": str(m.get("value") or "")}
            )
    return {
        "slug": str(p.get("slug") or ""),
        "name": str(p.get("name") or ""),
        "summary": _truncate(str(p.get("summary") or ""), summary_chars),
        "tags": [str(t) for t in (p.get("tags") or [])[:10] if t],
        "metrics": slim_metrics,
        "sort_order": int(p.get("sort_order") or 0),
        "virtual": bool(p.get("virtual")),
        "source": str(p.get("source") or ("db" if not p.get("virtual") else "index")),
    }


def _doc_row(hit: dict[str, Any], *, idx: int) -> dict[str, Any]:
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    content = str(
        hit.get("content_text")
        or hit.get("content")
        or hit.get("text")
        or ""
    )
    # search store may put text under different keys
    if not content and isinstance(hit.get("document"), str):
        content = hit["document"]
    ref = str(meta.get("ref") or f"doc:{idx}")
    return {
        "ref": ref,
        "title": str(meta.get("title") or meta.get("slug_hint") or ref)[:120],
        "slug_hint": str(meta.get("slug_hint") or ""),
        "excerpt": _truncate(content, 700),
        "score": float(hit.get("similarity") or hit.get("score") or 0.0),
        "kind": str(meta.get("kind") or meta.get("source") or ""),
    }


def _merge_doc_hits(hit_lists: list[list[dict[str, Any]]], *, limit: int) -> list[dict[str, Any]]:
    """RRF-ish merge by ref: first-seen order with score max."""
    by_ref: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for hits in hit_lists:
        for i, h in enumerate(hits or []):
            if not isinstance(h, dict):
                continue
            row = _doc_row(h, idx=i)
            ref = row["ref"]
            if not ref:
                continue
            if ref not in by_ref:
                by_ref[ref] = row
                order.append(ref)
            else:
                prev = by_ref[ref]
                if float(row.get("score") or 0) > float(prev.get("score") or 0):
                    by_ref[ref] = {**prev, **row}
    return [by_ref[r] for r in order[:limit]]


async def build_evidence_pack(
    query: str,
    *,
    tenant_id: int = 1,
    top_k_docs: int = 24,
    top_k_projects: int = 20,
    web_enrich: bool = False,
    company: str = "",
    role: str = "",
    **_deprecated: Any,
) -> dict[str, Any]:
    """Retrieve tenant portfolio **context** for layout planning only.

    Sources (fail-open each) — never returned as bake tool output:
    - ``portfolio_projects`` (full active inventory, ranked for the query)
    - ``portfolio_plugin__context`` RAG (multi-query over JD / company / role)
    - virtual project candidates synthesized from index hits not already in DB
    - optional web (company/role signal only)

    ``portfolio_star`` is deliberately unused (stale store).
    ``top_k_stars`` in kwargs is ignored for back-compat.
    """
    _ = _deprecated.pop("top_k_stars", None)
    q = (query or "").strip()
    projects: list[dict[str, Any]] = []
    docs: list[dict[str, Any]] = []
    virtual: list[dict[str, Any]] = []
    web: list[dict[str, Any]] = []
    inventory: dict[str, Any] = {}
    errors: list[str] = []

    # --- Projects: substantive inventory only (stubs demoted / excluded) ---
    try:
        from plugins.portfolio_plugin.store import list_projects
        from plugins.portfolio_plugin.compose.composer import rank_projects_by_query
        from plugins.portfolio_plugin.compose.quality import (
            filter_projects_for_layout,
            is_stub_summary,
        )

        raw = await list_projects(include_inactive=False, tenant_id=int(tenant_id))
        inventory["project_count"] = len(raw or [])
        inventory["project_slugs"] = [
            str(p.get("slug") or "") for p in (raw or []) if p.get("slug")
        ]
        stub_n = sum(
            1
            for p in (raw or [])
            if isinstance(p, dict) and is_stub_summary(str(p.get("summary") or ""))
        )
        inventory["project_stub_count"] = stub_n
        # Context for the agent: drop thin GitHub stubs so bake doesn't plan
        # school labs / empty READMEs as "projects".
        worthy = filter_projects_for_layout(raw or [])
        inventory["project_worthy_count"] = len(worthy)
        ranked = await rank_projects_by_query(
            worthy or [],
            q or "portfolio projects",
            tenant_id=int(tenant_id),
            top_k=max(int(top_k_projects), len(worthy or []) or 1),
        )
        seen_slugs: set[str] = set()
        ordered: list[dict] = []
        for p in ranked or []:
            slug = str(p.get("slug") or "").lower()
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            ordered.append(p)
        for p in worthy or []:
            slug = str(p.get("slug") or "").lower()
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            ordered.append(p)
        cap = max(int(top_k_projects), min(len(ordered), 28))
        projects = [
            _project_row(p, summary_chars=480)
            for p in ordered[:cap]
            if p.get("slug")
        ]
    except Exception as exc:
        logger.warning("evidence_pack projects failed open: %s", exc)
        errors.append(f"projects:{exc}"[:200])

    # --- Context corpus: multi-query RAG over portfolio_plugin__context ---
    queries: list[str] = []
    if q:
        queries.append(q[:800])
    cr = " ".join(x for x in (company, role) if x).strip()
    if cr and cr.lower() not in (q or "").lower():
        queries.append(cr[:400])
    if role and role.strip() and role.strip() not in queries:
        queries.append(f"{role.strip()} portfolio systems architecture"[:400])
    if not queries:
        queries = ["portfolio projects architecture systems"]

    try:
        from plugins.portfolio_plugin.discovery.index import search_context

        hit_lists: list[list[dict[str, Any]]] = []
        per_q = max(8, int(top_k_docs))
        for qq in queries[:4]:
            try:
                hits = await search_context(
                    qq, tenant_id=int(tenant_id), top_k=per_q
                )
                hit_lists.append(
                    [h for h in (hits or []) if isinstance(h, dict)]
                )
            except Exception as exc:
                errors.append(f"docs_q:{exc}"[:160])
        from plugins.portfolio_plugin.compose.quality import filter_docs_for_context

        merged = _merge_doc_hits(hit_lists, limit=max(int(top_k_docs) * 2, 24))
        docs = filter_docs_for_context(merged, limit=max(int(top_k_docs), 12))
        inventory["context_docs_in_pack"] = len(docs)
        inventory["context_docs_merged_raw"] = len(merged)
    except Exception as exc:
        logger.warning("evidence_pack docs failed open: %s", exc)
        errors.append(f"docs:{exc}"[:200])

    # Corpus size snapshot (agent knows the store is larger than the pack slice)
    try:
        from db_layer.search_content_vectors_store import list_search_content_vectors
        from plugins.portfolio_plugin.discovery.index import CONTEXT_COLLECTION

        sample = await list_search_content_vectors(
            CONTEXT_COLLECTION, limit=200, offset=0, tenant_id=int(tenant_id)
        )
        inventory["context_collection"] = CONTEXT_COLLECTION
        inventory["context_index_count"] = len(sample or [])
        kinds: dict[str, int] = {}
        for row in sample or []:
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            k = str(meta.get("kind") or meta.get("source") or "unknown")
            kinds[k] = kinds.get(k, 0) + 1
        inventory["context_kinds"] = kinds
    except Exception as exc:
        logger.debug("evidence_pack inventory list fail-open: %s", exc)

    # --- Virtual projects from index hits not already in portfolio_projects ---
    try:
        from plugins.portfolio_plugin.compose.block_builder import (
            _virtual_project_from_hit,
        )

        db_slugs = {
            str(p.get("slug") or "").lower()
            for p in projects
            if isinstance(p, dict)
        }
        # Reconstruct hit-shaped dicts from docs for virtual synthesis
        for d in docs:
            if not isinstance(d, dict):
                continue
            fake_hit = {
                "content_text": d.get("excerpt") or "",
                "metadata": {
                    "ref": d.get("ref"),
                    "title": d.get("title"),
                    "slug_hint": d.get("slug_hint"),
                    "kind": d.get("kind"),
                },
                "similarity": d.get("score") or 0.0,
            }
            vp = _virtual_project_from_hit(fake_hit)
            if not vp:
                continue
            from plugins.portfolio_plugin.compose.quality import (
                is_portfolio_worthy_project,
                is_stub_summary,
            )

            if is_stub_summary(str(vp.get("summary") or "")):
                continue
            if not is_portfolio_worthy_project(vp):
                continue
            slug = str(vp.get("slug") or "").lower()
            if not slug or slug in db_slugs:
                continue
            db_slugs.add(slug)
            vp["virtual"] = True
            vp["source"] = "index"
            virtual.append(_project_row(vp, summary_chars=400))
            if len(virtual) >= max(4, int(top_k_projects) // 3):
                break
        inventory["virtual_project_count"] = len(virtual)
    except Exception as exc:
        logger.debug("evidence_pack virtual fail-open: %s", exc)

    if web_enrich and (company or role or q):
        try:
            web = await _optional_web_snippets(company=company, role=role, query=q)
        except Exception as exc:
            logger.debug("evidence_pack web failed open: %s", exc)
            errors.append(f"web:{exc}"[:200])

    pack = {
        "status": "ok",
        "query": q[:500],
        "tenant_id": int(tenant_id),
        "projects": projects,
        "virtual_projects": virtual,
        "docs": docs,
        "web": web,
        "inventory": inventory,
        "catalog": block_catalog_entries(),
        "errors": errors,
        # Planner-only flag: callers must not surface projects/docs as tool output.
        "context_only": True,
    }
    pack["pack_hash"] = _pack_hash(pack)
    return pack


def _pack_hash(pack: dict[str, Any]) -> str:
    slim = {
        "projects": [p.get("slug") for p in (pack.get("projects") or [])],
        "virtual": [p.get("slug") for p in (pack.get("virtual_projects") or [])],
        "docs": [d.get("ref") for d in (pack.get("docs") or [])],
        "web": [w.get("url") for w in (pack.get("web") or [])],
    }
    raw = json.dumps(slim, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def _optional_web_snippets(
    *,
    company: str,
    role: str,
    query: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Fail-open company/role web snippets for soft job signal (not portfolio truth)."""
    q = " ".join(x for x in (company, role) if x).strip() or query[:120]
    if not q:
        return []

    # Dispatched through the catalog rather than imported: search_plugin may be
    # boot-excluded, and a cross-plugin import would reintroduce the coupling.
    try:
        from core.route_registry.execute import execute_operation

        res = await execute_operation(
            "search_plugin",
            "web_search",
            {
                "query": q,
                "max_results": limit,
                "_response_shape": {"response_format": "json"},
            },
            caller_scopes=None,
        )
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    if isinstance(res, dict):
        items = res.get("results") or res.get("data") or res.get("hits") or []
    elif isinstance(res, list):
        items = res
    else:
        items = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        rows.append(
            {
                "title": str(it.get("title") or "")[:160],
                "url": str(it.get("url") or it.get("href") or "")[:300],
                "snippet": _truncate(str(it.get("snippet") or it.get("content") or ""), 240),
            }
        )
    return rows


def format_evidence_budget(pack: dict[str, Any] | None, *, max_chars: int = 9000) -> str:
    """Render multi-store evidence as **planner context only** (not page output).

    Includes project inventory + discovery-index docs + virtual candidates.
    Does not include the retired ``portfolio_star`` store.
    """
    if not isinstance(pack, dict):
        return ""
    inv = pack.get("inventory") if isinstance(pack.get("inventory"), dict) else {}
    parts: list[str] = [
        "CONTEXT ONLY (not the page). Two stores feed this pack:",
        "  1) portfolio_projects — structured inventory",
        "  2) portfolio_plugin__context — discovery RAG corpus",
        "Use them to **choose** slugs/refs and author GenUI blocks.",
        "Do **not** dump the inventory as the layout; do **not** invent metrics.",
        "SKIP thin GitHub stubs (summary like 'GitHub repository owner/repo'), school labs, "
        "Vite templates, and empty READMEs — they are noise, not portfolio proof.",
        "Prefer case_study / contribution docs and primary-tagged projects over bare github rows.",
        "Prefer diverse slugs/refs (not 8 cards all named Helix). Deduplicate titles.",
        "Deep-dive prose must use a distinct context excerpt — never the same project.summary "
        "as both card body and prose. Card body must be real prose, not 'Period Covered' or tables.",
        "starStory only when you author grounded S/T/A/R props + source_refs from this pack.",
        f"pack_hash={pack.get('pack_hash') or ''}",
    ]
    if inv:
        kinds = inv.get("context_kinds") if isinstance(inv.get("context_kinds"), dict) else {}
        kinds_s = ", ".join(f"{k}:{v}" for k, v in list(kinds.items())[:12])
        parts.append(
            "### Inventory snapshot (context, not output)\n"
            f"- portfolio_projects: {inv.get('project_count', '?')} total, "
            f"{inv.get('project_worthy_count', '?')} worthy, "
            f"{inv.get('project_stub_count', '?')} stubs filtered\n"
            f"- worthy slugs in pack: [{', '.join(p.get('slug','') for p in (pack.get('projects') or []) if isinstance(p, dict))}]\n"
            f"- context_index ({inv.get('context_collection') or 'portfolio_plugin__context'}): "
            f"~{inv.get('context_index_count', '?')} rows; kinds=[{kinds_s}]\n"
            f"- virtual_projects: {inv.get('virtual_project_count', 0)}"
        )

    projects = pack.get("projects") or []
    if projects:
        parts.append("### Projects context (DB inventory, query-ranked)")
        for p in projects:
            if not isinstance(p, dict):
                continue
            tags = ",".join(p.get("tags") or [])
            mets = "; ".join(
                f"{m.get('label')}={m.get('value')}"
                for m in (p.get("metrics") or [])
                if isinstance(m, dict)
            )
            parts.append(
                f"- slug=`{p.get('slug')}` name={p.get('name')} "
                f"sort={p.get('sort_order')} tags=[{tags}]\n"
                f"  summary: {p.get('summary')}\n"
                f"  metrics: {mets or 'n/a'}"
            )

    virtual = pack.get("virtual_projects") or []
    if virtual:
        parts.append(
            "### Virtual projects context (index only — not yet in portfolio_projects)"
        )
        for p in virtual[:12]:
            if not isinstance(p, dict):
                continue
            parts.append(
                f"- slug=`{p.get('slug')}` name={p.get('name')} "
                f"tags=[{','.join(p.get('tags') or [])}]\n"
                f"  summary: {p.get('summary')}"
            )

    docs = pack.get("docs") or []
    if docs:
        parts.append("### Context docs (RAG over portfolio_plugin__context)")
        for d in docs[:20]:
            if not isinstance(d, dict):
                continue
            parts.append(
                f"- ref=`{d.get('ref')}` kind={d.get('kind')} "
                f"slug_hint={d.get('slug_hint')} title={d.get('title')}\n"
                f"  {d.get('excerpt')}"
            )
    web = pack.get("web") or []
    if web:
        parts.append("### Optional web (company/role signal only — not portfolio truth)")
        for w in web[:3]:
            if not isinstance(w, dict):
                continue
            parts.append(f"- {w.get('title')} | {w.get('url')}\n  {w.get('snippet')}")

    text = "\n".join(parts)
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def recipe_type_fingerprint(recipe: dict[str, Any] | None) -> list[str]:
    """Ordered block_type list from a recipe skeleton (for anti-clone checks)."""
    if not isinstance(recipe, dict):
        return []
    sk = recipe.get("skeleton") or []
    out: list[str] = []
    for step in sk:
        if not isinstance(step, dict):
            continue
        t = str(step.get("block_type") or step.get("type") or "").strip()
        if t:
            out.append(t)
    return out


def plan_type_fingerprint(plan: dict[str, Any] | None) -> list[str]:
    """Ordered block_type list from a LayoutPlan dict or layout blocks."""
    if not isinstance(plan, dict):
        return []
    steps = plan.get("steps")
    if isinstance(steps, list) and steps:
        out: list[str] = []
        for s in steps:
            if not isinstance(s, dict):
                continue
            t = str(s.get("block_type") or s.get("type") or "").strip()
            if t:
                out.append(t)
        return out
    blocks = plan.get("blocks")
    if isinstance(blocks, list):
        return [
            str(b.get("type") or "").strip()
            for b in blocks
            if isinstance(b, dict) and b.get("type")
        ]
    return []


def is_skeleton_clone(
    plan_or_layout: dict[str, Any] | None,
    recipe: dict[str, Any] | None,
    *,
    min_len: int = 5,
) -> bool:
    """True when plan type sequence matches recipe skeleton (exact order)."""
    a = plan_type_fingerprint(plan_or_layout)
    b = recipe_type_fingerprint(recipe)
    if len(a) < min_len or len(b) < min_len:
        return False
    # Compare collapsing consecutive duplicate types (card,card,card → card)
    def collapse(seq: list[str]) -> list[str]:
        out: list[str] = []
        for t in seq:
            if not out or out[-1] != t:
                out.append(t)
        return out

    return collapse(a) == collapse(b)


def is_floor_clone(
    plan_or_layout: dict[str, Any] | None,
    floor_blocks: list[dict[str, Any]] | list[str] | None,
    *,
    min_len: int = 5,
) -> bool:
    """True when plan type sequence matches floor composer block types."""
    a = plan_type_fingerprint(plan_or_layout)
    if isinstance(floor_blocks, list) and floor_blocks and isinstance(floor_blocks[0], str):
        b = [str(x) for x in floor_blocks if x]
    elif isinstance(floor_blocks, list):
        b = [
            str(b.get("type") or b.get("block_type") or "").strip()
            for b in floor_blocks
            if isinstance(b, dict)
        ]
        b = [x for x in b if x]
    else:
        b = []
    if len(a) < min_len or len(b) < min_len:
        return False

    def collapse(seq: list[str]) -> list[str]:
        out: list[str] = []
        for t in seq:
            if not out or out[-1] != t:
                out.append(t)
        return out

    return collapse(a) == collapse(b)
