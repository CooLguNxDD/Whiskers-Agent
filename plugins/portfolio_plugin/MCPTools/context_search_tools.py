"""MCP tools letting the GOAP agent read the indexed context corpus and build
scoped, cited layout blocks — the retrieve → build → assemble loop.

Unlike ``discover_portfolio_context`` / ``rebuild_portfolio_index`` (write
tools, GOAP-denylisted), ``search_portfolio_context`` is read-only and
GOAP-visible: it is the one discovery-adjacent tool the planner should reach
for during a visitor/agent turn.
"""

import logging

from core.context import mcp, current_tenant_id
from plugins.portfolio_plugin.discovery.index import search_context

logger = logging.getLogger("whiskers.plugins.portfolio")

_EXCERPT_CHARS = 600


def _excerpt(hit: dict) -> dict:
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    text = hit.get("content_text") or ""
    return {
        "ref": meta.get("ref"),
        "kind": meta.get("kind"),
        "source": meta.get("source"),
        "title": meta.get("title"),
        "slug_hint": meta.get("slug_hint"),
        "url": meta.get("url"),
        "updated_at": meta.get("updated_at"),
        "tags": meta.get("tags") or [],
        "similarity": hit.get("similarity"),
        "excerpt": str(text)[:_EXCERPT_CHARS],
    }


def _matches(hit: dict, *, kinds: list[str] | None, sources: list[str] | None, slugs: list[str] | None) -> bool:
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    if kinds:
        kind_set = {str(k).strip().lower() for k in kinds if str(k).strip()}
        if str(meta.get("kind") or "").lower() not in kind_set:
            return False
    if sources:
        src_set = {str(s).strip().lower() for s in sources if str(s).strip()}
        if str(meta.get("source") or "").lower() not in src_set:
            return False
    if slugs:
        slug_set = {str(s).strip().lower() for s in slugs if str(s).strip()}
        hint = str(meta.get("slug_hint") or "").lower()
        ref = str(meta.get("ref") or "").lower()
        if not any(s == hint or s in ref for s in slug_set):
            return False
    return True


@mcp.tool(
    title="search_portfolio_context",
    tags={"portfolio_plugin", "read", "ask"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def search_portfolio_context(
    query: str,
    top_k: int = 8,
    kinds: list[str] | None = None,
    sources: list[str] | None = None,
    slugs: list[str] | None = None,
) -> dict:
    """Semantically search the indexed portfolio context corpus (READMEs,
    Notion pages, releases, ...) — evidence base for ``build_layout_block``.

    Returns short excerpts (not full docs) with citable ``ref`` fields.
    Optionally filter by doc ``kinds`` (repo/readme/page/release/commitlog),
    ``sources`` (github/notion/url), or project ``slugs``. Empty result means
    the index has nothing relevant — fall back to ``get_projects`` /
    ``build_layout_block`` without a query (DB-only selection).
    """
    if not query or not str(query).strip():
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["query"]}

    tenant_id = current_tenant_id.get()
    top_k = max(1, min(int(top_k or 8), 30))
    over_fetch = max(top_k * 3, 15) if (kinds or sources or slugs) else top_k

    hits = await search_context(str(query), tenant_id=tenant_id, top_k=over_fetch)
    filtered = [h for h in hits if _matches(h, kinds=kinds, sources=sources, slugs=slugs)][:top_k]

    return {
        "status": "ok",
        "query": query,
        "count": len(filtered),
        "docs": [_excerpt(h) for h in filtered],
    }


@mcp.tool(
    title="build_layout_block",
    tags={"portfolio_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": False},
)
async def build_layout_block(
    block_type: str,
    query: str = "",
    slugs: list[str] | None = None,
    top_k: int = 6,
    props: dict | None = None,
    source_refs: list[str] | None = None,
    block_id: str = "",
    layout: dict | None = None,
    kind: str = "auto",
) -> dict:
    """Build ONE validated, scoped layout block — the unit of agentic composition.

    DB/context-derived types (hero/statStrip/projectGrid/kpiGrid/timeline/
    starStory/thin-archDiagram) select their own content: pass ``slugs`` to
    pick exact projects, or ``query`` to semantically scope (never renders
    the whole inventory unless both are omitted). Citations are derived
    automatically.

    Agent-authored types (prose/chart/comparison/codeSnippet/composite/
    archDiagram-with-a-real-source) take your ``props`` directly, but REQUIRE
    ``source_refs`` citing docs returned by ``search_portfolio_context`` or a
    project's declared context_sources — ungrounded content is rejected.

    ``kind`` overrides which path a type takes: ``"auto"`` (default) picks the
    type's normal path; ``"authored"`` forces even a normally DB-derived type
    (e.g. ``chart``) through the props+source_refs path so you can author a
    narrative version instead of the auto-derived one; ``"db"`` forces the
    DB-derived path and errors for purely-authored types (prose/comparison/
    codeSnippet/composite).

    Optional ``layout`` ``{span: 1-12, order?: int}`` drives the CatPortfolio
    12-column grid (span 12 full width; 6 half; 8+4 spotlight pairs).

    Accumulate blocks across calls (they ride in your working memory as
    plain dicts) then pass the list as ``design_layout(spec={"sections":[...]})``
    to assemble the final page. Never invent project data or refs.
    """
    from plugins.portfolio_plugin.compose.block_builder import build_layout_block_impl

    tenant_id = current_tenant_id.get()
    return await build_layout_block_impl(
        block_type,
        tenant_id=tenant_id,
        query=query,
        slugs=slugs,
        top_k=top_k,
        props=props,
        source_refs=source_refs,
        block_id=block_id,
        layout=layout,
        kind=kind,
    )
