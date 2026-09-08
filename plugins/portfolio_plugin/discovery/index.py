"""Index ContextDocs into portfolio_plugin__context (search_content_vectors)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.memory.registry import plugin_collection
from plugins.portfolio_plugin.discovery.normalize import ContextDoc

logger = logging.getLogger("whiskers.plugins.portfolio.discovery")

CONTEXT_COLLECTION = plugin_collection("portfolio_plugin", "context")  # portfolio_plugin__context


async def index_docs(
    docs: list[ContextDoc],
    *,
    tenant_id: int,
    force: bool = False,
    replace_stale: bool = True,
) -> dict[str, Any]:
    """Embed and store docs; sha256 dedupe is built into the store.

    ``replace_stale`` (default on): before inserting a doc for a given
    ``ref``, delete prior rows for ``(collection, ref, tenant)``. Without
    this, content_hash dedupe means a *changed* README never actually
    replaces the old vector row — ``meta.indexed_at`` stays at first-index
    time forever and ``is_fresh()`` never sees the update. ``force`` is an
    alias kept for back-compat call sites; either flag triggers replacement.
    """
    from db_layer.search_content_vectors_store import (
        add_search_content_vector,
        delete_search_content_vectors_by_meta,
    )

    indexed = 0
    deduped = 0
    replaced = 0
    errors: list[str] = []
    do_replace = bool(force) or bool(replace_stale)

    for doc in docs:
        if not doc.text or not doc.text.strip():
            continue
        meta = doc.to_metadata()
        meta["indexed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if do_replace and doc.ref:
            try:
                n = await delete_search_content_vectors_by_meta(
                    CONTEXT_COLLECTION, "ref", doc.ref, tenant_id=tenant_id
                )
                replaced += n
            except Exception as exc:
                logger.warning("discovery stale-replace failed for %s: %s", doc.ref, exc)
        try:
            res = await add_search_content_vector(
                CONTEXT_COLLECTION,
                doc.text,
                metadata=meta,
                tenant_id=tenant_id,
            )
            if isinstance(res, dict) and res.get("deduped"):
                deduped += 1
            else:
                indexed += 1
        except Exception as exc:
            msg = f"{doc.kind}:{doc.ref}: {exc}"
            logger.warning("discovery index failed: %s", msg)
            errors.append(msg[:300])

    return {
        "status": "ok",
        "collection": CONTEXT_COLLECTION,
        "indexed": indexed,
        "deduped": deduped,
        "replaced": replaced,
        "errors": errors,
        "total_docs": len(docs),
    }


async def search_context(
    query: str,
    *,
    tenant_id: int,
    top_k: int = 12,
) -> list[dict[str, Any]]:
    """Semantic search over the portfolio context collection."""
    from db_layer.search_content_vectors_store import search_search_content_vectors

    if not query or not str(query).strip():
        return []
    try:
        return await search_search_content_vectors(
            str(query),
            CONTEXT_COLLECTION,
            top_k=top_k,
            tenant_id=tenant_id,
        )
    except Exception as exc:
        logger.warning("discovery context search failed: %s", exc)
        return []


async def docs_for_slug(
    slug: str,
    *,
    tenant_id: int,
    top_k: int = 8,
    extra_refs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch indexed docs that match a project slug (metadata filter after search).

    ``extra_refs`` lets callers pass a project's declared ``context_sources``
    refs / link hrefs — the slug alone can't bridge hyphen/underscore/no-sep
    formats (e.g. ``oct`` vs a discovered ``opencat-mcp-full``), so matching
    also checks those refs against the indexed doc's own ``ref``.
    """
    from plugins.portfolio_plugin.discovery.slug import keys_match

    hits = await search_context(slug, tenant_id=tenant_id, top_k=max(top_k * 2, 12))
    matched: list[dict[str, Any]] = []
    for h in hits:
        meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
        hint = str(meta.get("slug_hint") or "")
        ref = str(meta.get("ref") or "")
        title = str(meta.get("title") or "")
        hit_match = keys_match(slug, hint, ref, title) or any(
            keys_match(er, ref) for er in (extra_refs or []) if er
        )
        if hit_match:
            matched.append(h)
        if len(matched) >= top_k:
            break
    return matched


def newest_indexed_at(hits: list[dict[str, Any]]) -> datetime | None:
    """Parse the newest indexed_at / updated_at from hit metadata."""
    best: datetime | None = None
    for h in hits:
        meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
        for key in ("indexed_at", "updated_at"):
            raw = meta.get(key)
            if not raw or not isinstance(raw, str):
                continue
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if best is None or dt > best:
                    best = dt
            except Exception:
                logger.debug("index.py: continue after exception", exc_info=True)
                continue
        # also created_at from list shape
        created = h.get("created_at")
        if isinstance(created, str):
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if best is None or dt > best:
                    best = dt
            except Exception:
                logger.debug("index.py: swallowed exception", exc_info=True)
    return best


def is_fresh(hits: list[dict[str, Any]], freshness_s: float) -> bool:
    """True when newest hit is within freshness_s seconds."""
    if not hits:
        return False
    newest = newest_indexed_at(hits)
    if newest is None:
        # have hits but no timestamp — treat as usable but not "fresh" enough to skip live
        return False
    now = datetime.now(timezone.utc)
    age = (now - newest).total_seconds()
    return age <= float(freshness_s)
