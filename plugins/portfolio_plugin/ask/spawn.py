"""Map discovery-job candidates onto virtual project dicts for the tank.

Spawned rows never hit ``portfolio_projects``. They match the shape
``compose/block_builder._virtual_project_from_hit`` already emits, plus a
``discovered`` tag so CatPortfolio can style a fresh specimen without a
``FishSpec`` schema change.
"""

from __future__ import annotations

from typing import Any


def _href_from_ref(ref: str, source: str) -> str:
    """Derive a clickable href from a discovery ref (URL or ``owner/repo``)."""
    raw = str(ref or "").strip()
    if raw.startswith(("http://", "https://")):
        return raw
    if not raw:
        return ""
    kind = str(source or "").strip().lower()
    if kind == "notion":
        compact = raw.replace("-", "")
        return f"https://www.notion.so/{compact}" if compact else ""
    # GitHub (default): ``owner/repo`` or any slash-separated ref without spaces.
    if "/" in raw and " " not in raw and not raw.startswith("/"):
        return f"https://github.com/{raw.lstrip('/')}"
    return ""


def spawn_projects_from_candidates(candidates: list[dict] | None) -> list[dict]:
    """Turn scored discovery candidates into virtual project dicts.

    A ``links`` href derived from ``ref`` is what clears
    ``is_portfolio_worthy_project`` even when the summary is a stub.
    Candidates without a slug or a derivable href are dropped.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        slug = str(cand.get("slug") or "").strip()
        if not slug:
            continue
        key = slug.lower()
        if key in seen:
            continue
        ref = str(cand.get("ref") or "").strip()
        kind = str(cand.get("source") or "github").strip() or "github"
        href = _href_from_ref(ref, kind)
        if not href:
            continue
        tags = [str(t) for t in (cand.get("tags") or []) if t]
        if "discovered" not in {t.lower() for t in tags}:
            tags.append("discovered")
        name = str(cand.get("name") or slug).strip() or slug
        summary = str(cand.get("summary") or "").strip() or f"Discovered {kind} project."
        seen.add(key)
        out.append(
            {
                "slug": slug,
                "name": name,
                "summary": summary,
                "tags": tags,
                "metrics": [],
                "links": [{"label": kind, "href": href}],
                "context_sources": (
                    [{"id": f"disc:{kind}:{ref}", "kind": kind, "ref": ref}] if ref else []
                ),
                "virtual": True,
            }
        )
    return out
