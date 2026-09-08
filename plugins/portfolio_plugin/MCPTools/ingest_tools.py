"""MCP-only portfolio context ingestion (replaces scratch indexers)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.context import mcp
from plugins.portfolio_plugin.discovery.index import index_docs
from plugins.portfolio_plugin.discovery.normalize import ContextDoc, chunk_text, strip_directives
from plugins.portfolio_plugin.tenant import require_tenant_id

logger = logging.getLogger("whiskers.plugins.portfolio.ingest")


def _path_allowlist_roots() -> list[Path]:
    """Resolve ingest path sandbox roots (no cwd /tmp — monorepo-wide reads)."""
    roots: list[Path] = []
    # Plugin package dir (fixtures under plugins/portfolio_plugin/).
    try:
        roots.append(Path(__file__).resolve().parents[1])
    except OSError as exc:
        logger.debug("ingest allowlist: package root unavailable: %s", exc)
    # Config-defined ingest root from plugin settings (optional).
    try:
        from plugins.portfolio_plugin.plugin_config import SETTINGS

        raw = (SETTINGS or {}).get("ingest_root") if isinstance(SETTINGS, dict) else None
        if isinstance(raw, str) and raw.strip():
            roots.append(Path(raw).expanduser().resolve())
    except Exception as exc:
        logger.debug("ingest allowlist: settings.ingest_root skipped: %s", exc)
    return roots


def _safe_path(raw: str) -> Path | None:
    """Return a resolved path only if it sits under an allowlisted root."""
    try:
        p = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    for root in _path_allowlist_roots():
        try:
            r = root.resolve()
            if p == r or r in p.parents:
                return p
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("ingest path root resolve failed: %s", exc)
            continue
    return None


def _clean_title(title: str) -> str:
    """Strip (n/m) chunk-part suffixes from titles before storage."""
    import re

    t = (title or "").strip()
    t = re.sub(r"\s*\(\d+/\d+\)\s*$", "", t).strip()
    return t or "document"


async def _doc_from_entry(entry: dict[str, Any], *, idx: int) -> list[ContextDoc]:
    """Expand one ingest entry into one or more ContextDocs (chunked)."""
    if not isinstance(entry, dict):
        return []
    text = entry.get("text")
    url = entry.get("url")
    path = entry.get("path")
    slug_hint = str(entry.get("slug_hint") or entry.get("slug") or "").strip() or None
    title = _clean_title(str(entry.get("title") or slug_hint or f"doc-{idx}"))
    kind = str(entry.get("kind") or "page")
    tags = [str(t) for t in (entry.get("tags") or []) if t]
    source = "ingest"

    body = ""
    ref = ""
    if isinstance(text, str) and text.strip():
        body = text
        ref = str(entry.get("ref") or f"ingest:text:{slug_hint or idx}")
    elif isinstance(url, str) and url.startswith(("http://", "https://")):
        try:
            from core.proxy.proxy_manager import _safe_async_client

            async with _safe_async_client() as client:
                resp = await client.get(url, timeout=30.0)
                resp.raise_for_status()
                body = resp.text or ""
            ref = f"url:{url}"
            source = "url"
            kind = kind or "page"
        except Exception as exc:
            logger.warning("ingest url failed: %s", exc)
            return []
    elif isinstance(path, str) and path.strip():
        sp = _safe_path(path)
        if sp is None or not sp.is_file():
            logger.warning("ingest path denied or missing: %s", path)
            return []
        try:
            body = sp.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("ingest path read failed: %s", exc)
            return []
        ref = f"local:{sp.name}"
        source = "path"
    else:
        return []

    body = strip_directives(body)
    if not body.strip():
        return []

    chunks = chunk_text(body, max_chars=10_000)
    docs: list[ContextDoc] = []
    n = len(chunks)
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        part_ref = ref if n == 1 else f"{ref}#part{i + 1}"
        docs.append(
            ContextDoc(
                source=source,
                ref=part_ref,
                kind=kind,
                title=title,  # no (n/m) suffix
                text=chunk,
                url=url if isinstance(url, str) else None,
                slug_hint=slug_hint,
                tags=tags,
            )
        )
    return docs


@mcp.tool(
    title="ingest_portfolio_context",
    tags={"portfolio_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def ingest_portfolio_context(
    docs: list[dict] | None = None,
    force: bool = False,
    replace_stale: bool = True,
) -> dict:
    """Chunk + index arbitrary text/url/path content into portfolio_plugin__context.

    Each doc entry: ``{text|url|path, slug_hint, title?, tags?, kind?, ref?}``.
    Tenant is always taken from the authenticated principal — never a caller
    argument. GOAP-denylisted operator tool.
    """
    tid = require_tenant_id()
    if tid is None:
        return {"status": "error", "error": "tenant_unresolved"}

    entries = docs if isinstance(docs, list) else []
    if not entries:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["docs"],
        }

    built: list[ContextDoc] = []
    errors: list[str] = []
    for i, entry in enumerate(entries):
        try:
            built.extend(await _doc_from_entry(entry, idx=i))
        except Exception as exc:
            errors.append(f"entry[{i}]:{exc}"[:200])

    if not built:
        return {"status": "error", "error": "no_docs_built", "errors": errors}

    result = await index_docs(
        built,
        tenant_id=tid,
        force=bool(force),
        replace_stale=bool(replace_stale),
    )
    result["input_entries"] = len(entries)
    result["built_docs"] = len(built)
    if errors:
        result.setdefault("errors", []).extend(errors)
    return result


@mcp.tool(
    title="reconcile_portfolio_projects",
    tags={"portfolio_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def reconcile_portfolio_projects(
    scope: str = "",
    dry_run: bool = True,
) -> dict:
    """Promote indexed slug_hints into portfolio_projects rows.

    Uses discovery reconcile plan/apply. Never overwrites hand-curated
    summary/metrics. Tenant from principal only. GOAP-denylisted operator tool.
    """
    tid = require_tenant_id()
    if tid is None:
        return {"status": "error", "error": "tenant_unresolved"}

    from plugins.portfolio_plugin.store import list_projects
    from db_layer.search_content_vectors_store import list_search_content_vectors
    from plugins.portfolio_plugin.discovery.index import CONTEXT_COLLECTION
    from plugins.portfolio_plugin.discovery.normalize import ContextDoc
    from plugins.portfolio_plugin.discovery.reconcile import apply_reconcile, plan_reconcile

    # Load recent context vectors as synthetic ContextDocs for reconcile
    try:
        rows = await list_search_content_vectors(
            CONTEXT_COLLECTION, tenant_id=tid, limit=200
        )
    except Exception:
        # Fallback: empty list API may not exist — try search with broad query
        try:
            from plugins.portfolio_plugin.discovery.index import search_context

            hits = await search_context(scope or "portfolio project", tenant_id=tid, top_k=50)
            rows = hits or []
        except Exception as exc:
            return {"status": "error", "error": f"list_context_failed:{exc}"[:200]}

    docs: list[ContextDoc] = []
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        text = str(row.get("content_text") or row.get("content") or row.get("text") or row.get("document") or "")
        if not text.strip():
            continue
        slug_hint = str(meta.get("slug_hint") or "").strip()
        if scope and scope.lower() not in (
            slug_hint.lower(),
            str(meta.get("ref") or "").lower(),
            str(meta.get("title") or "").lower(),
        ):
            # soft filter: keep if scope substring appears
            blob = f"{slug_hint} {meta.get('ref')} {meta.get('title')} {text[:200]}".lower()
            if scope.lower() not in blob:
                continue
        docs.append(
            ContextDoc(
                source=str(meta.get("source") or "search"),
                ref=str(meta.get("ref") or f"ctx:{i}"),
                kind=str(meta.get("kind") or "page"),
                title=str(meta.get("title") or slug_hint or f"ctx-{i}"),
                text=text[:5000],
                url=meta.get("url"),
                slug_hint=slug_hint or None,
                tags=list(meta.get("tags") or []) if isinstance(meta.get("tags"), list) else [],
            )
        )

    existing = await list_projects(include_inactive=True, tenant_id=tid)
    plan = plan_reconcile(docs, existing or [])
    if dry_run:
        return {
            "status": "ok",
            "dry_run": True,
            "plan": plan,
            "doc_count": len(docs),
        }
    applied = await apply_reconcile(plan, tenant_id=tid, write_back=True)
    return {
        "status": "ok",
        "dry_run": False,
        "plan": plan,
        "applied": applied,
        "doc_count": len(docs),
    }
