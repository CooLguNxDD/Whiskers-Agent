"""Reconcile ContextDocs → portfolio_projects (gated by write_back).

Provenance is stamped in context_sources[].id prefixes (disc:github:owner/repo)
so no migration is required. Hand-authored summary/metrics are never overwritten.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.compose.quality import is_stub_summary, sanitize_card_body
from plugins.portfolio_plugin.discovery.normalize import ContextDoc

logger = logging.getLogger("whiskers.plugins.portfolio.discovery")

PROVENANCE_PREFIX = "disc:"


def _source_id(doc: ContextDoc) -> str:
    return f"{PROVENANCE_PREFIX}{doc.kind}:{doc.ref}"


def _context_source_entry(doc: ContextDoc) -> dict[str, Any]:
    """Build a context_sources entry derived from a discovered doc."""
    entry: dict[str, Any] = {
        "id": _source_id(doc),
        "kind": doc.kind,
        "ref": doc.ref,
        "use": ["meta", "readme"] if doc.kind == "github" else ["content"],
    }
    if doc.url:
        entry["url"] = doc.url
    return entry


def _group_by_slug(docs: list[ContextDoc]) -> dict[str, list[ContextDoc]]:
    groups: dict[str, list[ContextDoc]] = {}
    for doc in docs:
        slug = (doc.slug_hint or "").strip() or "project"
        groups.setdefault(slug, []).append(doc)
    return groups


# Timeline source precedence — a hand-authored operator date always wins
# (checked separately via existing_row lock, never produced by discovery);
# among discovered sources, a Notion "Period Covered" line is more
# deliberate than GitHub repo activity dates.
_PERIOD_SOURCE_RANK = {"manual": 3, "notion_period": 2, "github": 1}


def _pick_period(group: list[ContextDoc]) -> tuple[str | None, str | None, str | None]:
    """Pick the best-sourced (started_on, ended_on, timeline_source) across a slug's docs."""
    best_source: str | None = None
    best_started: str | None = None
    best_ended: str | None = None
    best_rank = -1
    for doc in group:
        source = doc.period_source
        if not source or (not doc.started_on and not doc.ended_on):
            continue
        rank = _PERIOD_SOURCE_RANK.get(source, 0)
        if rank > best_rank:
            best_rank = rank
            best_source = source
            best_started = doc.started_on
            best_ended = doc.ended_on
    return best_started, best_ended, best_source


def plan_reconcile(
    docs: list[ContextDoc],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a reconcile plan without writing.

    Each entry: {action: create|update|skip, slug, name, summary?, tags?, links?,
                 context_sources?, reason?}
    """
    by_slug = {str(p.get("slug")): p for p in existing if p.get("slug")}
    plan: list[dict[str, Any]] = []

    for slug, group in _group_by_slug(docs).items():
        primary = group[0]
        name = primary.title or slug
        # Prefer a real prose paragraph as the summary seed — sanitize_card_body
        # already knows how to skip README chrome (badge walls, headings, table/
        # box-drawing noise, "Period Covered" chrome) that a naive first-line
        # walk would otherwise capture verbatim. portfolio_projects.summary is
        # NOT NULL — always fall back to a non-empty string.
        summary = ""
        for doc in group:
            body = sanitize_card_body(doc.text or "")
            if body:
                summary = body[:500]
                break
        if not summary.strip():
            summary = (name or slug or primary.ref or "Discovered project")[:500]
        tags: list[str] = []
        tag_set: set[str] = set()
        for doc in group:
            for t in doc.tags or []:
                tl = str(t).lower()
                if tl and tl not in tag_set:
                    tag_set.add(tl)
                    tags.append(str(t))
        links = []
        for doc in group:
            if doc.url:
                links.append({"label": doc.kind, "href": doc.url})
        sources = [_context_source_entry(doc) for doc in group]
        disc_started, disc_ended, disc_source = _pick_period(group)

        existing_row = by_slug.get(slug)
        if existing_row is None:
            plan.append(
                {
                    "action": "create",
                    "slug": slug,
                    "name": name,
                    "summary": summary,
                    "tags": tags,
                    "links": links,
                    "context_sources": sources,
                    "started_on": disc_started,
                    "ended_on": disc_ended,
                    "timeline_source": disc_source,
                }
            )
            continue

        # Present → non-destructive merge plan
        existing_summary = (existing_row.get("summary") or "").strip()
        existing_tags = list(existing_row.get("tags") or []) if isinstance(existing_row.get("tags"), list) else []
        existing_sources = (
            list(existing_row.get("context_sources") or [])
            if isinstance(existing_row.get("context_sources"), list)
            else []
        )
        existing_links = (
            list(existing_row.get("links") or [])
            if isinstance(existing_row.get("links"), list)
            else []
        )

        merged_tags = list(existing_tags)
        et = {str(t).lower() for t in existing_tags}
        for t in tags:
            if str(t).lower() not in et:
                merged_tags.append(t)
                et.add(str(t).lower())

        # Union context_sources by id
        src_by_id = {}
        for s in existing_sources:
            if isinstance(s, dict) and s.get("id"):
                src_by_id[str(s["id"])] = s
        for s in sources:
            src_by_id[str(s["id"])] = s
        merged_sources = list(src_by_id.values())

        # Union links by href
        link_hrefs = {
            str(l.get("href"))
            for l in existing_links
            if isinstance(l, dict) and l.get("href")
        }
        merged_links = list(existing_links)
        for l in links:
            if l.get("href") and str(l["href"]) not in link_hrefs:
                merged_links.append(l)
                link_hrefs.add(str(l["href"]))

        # Never overwrite a real, hand-authored/discovered summary — but a
        # frozen discovery stub (e.g. "GitHub repository X", bare "owner/repo")
        # is not real content, so it stays eligible for refresh indefinitely
        # rather than locking in the first stub forever the moment a row is
        # created. `summary` here is already stub-checked via sanitize_card_body
        # above, so it is never used to replace a real summary with a worse one.
        existing_is_stub = is_stub_summary(existing_summary)
        keep_existing = bool(existing_summary) and not existing_is_stub

        # Timeline: an operator-set date is locked and never touched by
        # discovery; otherwise take whichever of {existing, discovered}
        # outranks the other (notion_period beats github beats nothing).
        existing_source = existing_row.get("timeline_source")
        if existing_source == "manual":
            merged_started = existing_row.get("started_on")
            merged_ended = existing_row.get("ended_on")
            merged_source = existing_source
        else:
            existing_rank = _PERIOD_SOURCE_RANK.get(existing_source, 0)
            disc_rank = _PERIOD_SOURCE_RANK.get(disc_source, 0)
            if disc_source and disc_rank >= existing_rank:
                merged_started, merged_ended, merged_source = disc_started, disc_ended, disc_source
            else:
                merged_started = existing_row.get("started_on")
                merged_ended = existing_row.get("ended_on")
                merged_source = existing_source

        entry: dict[str, Any] = {
            "action": "update",
            "slug": slug,
            "name": existing_row.get("name") or name,
            "tags": merged_tags,
            "links": merged_links,
            "context_sources": merged_sources,
            "summary": existing_summary if keep_existing else summary,
            "preserve_summary": keep_existing,
            "preserve_metrics": True,
            "started_on": merged_started,
            "ended_on": merged_ended,
            "timeline_source": merged_source,
        }
        plan.append(entry)

    return plan


async def apply_reconcile(
    plan: list[dict[str, Any]],
    *,
    tenant_id: int,
    write_back: bool,
) -> dict[str, Any]:
    """Apply reconcile plan when write_back is True; otherwise no-op."""
    if not write_back:
        return {
            "status": "ok",
            "written": False,
            "created": 0,
            "updated": 0,
            "plan_size": len(plan),
        }

    from plugins.portfolio_plugin.store import upsert_project

    created = 0
    updated = 0
    errors: list[str] = []

    for entry in plan:
        action = entry.get("action")
        slug = entry.get("slug")
        if not slug or action not in ("create", "update"):
            continue
        fields: dict[str, Any] = {}
        if entry.get("name"):
            fields["name"] = entry["name"]
        else:
            fields["name"] = str(slug)
        if entry.get("tags") is not None:
            fields["tags"] = entry["tags"]
        if entry.get("links") is not None:
            fields["links"] = entry["links"]
        if entry.get("context_sources") is not None:
            fields["context_sources"] = entry["context_sources"]
        # started_on/ended_on/timeline_source: always pass through — entry
        # already carries the precedence-resolved values (existing dates on
        # a no-op path, discovered ones when they outrank existing), so an
        # unconditional set here can't regress a previously-set date.
        fields["started_on"] = entry.get("started_on")
        fields["ended_on"] = entry.get("ended_on")
        fields["timeline_source"] = entry.get("timeline_source")
        # Only set summary when creating or when existing was empty.
        # Column is NOT NULL — never omit on create.
        if action == "create" or not entry.get("preserve_summary"):
            summary = (entry.get("summary") or "").strip()
            if not summary:
                summary = str(entry.get("name") or slug)[:500]
            fields["summary"] = summary
        # metrics intentionally never set from discovery (preserve hand-authored)
        try:
            await upsert_project(str(slug), tenant_id=tenant_id, **fields)
            if action == "create":
                created += 1
            else:
                updated += 1
        except Exception as exc:
            msg = f"{slug}: {exc}"
            logger.warning("reconcile upsert failed: %s", msg)
            errors.append(msg[:300])

    return {
        "status": "ok",
        "written": True,
        "created": created,
        "updated": updated,
        "errors": errors,
        "plan_size": len(plan),
    }
