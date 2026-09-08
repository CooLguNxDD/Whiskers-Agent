"""Intent → scoped GenUI layout (no hardcoded regex fragment routing).

Primary path: ``compose_scoped_layout`` (retrieve/build/assemble via block
builders). Explicit ``page`` requests redirect to the floor composer (fragments
removed). Last resort: audience template ``compose_layout``.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.portfolio_plugin.compose.composer import (
    get_audience_template,
    infer_audience_from_job_signals,
    compose_layout,
)
from plugins.portfolio_plugin.compose.compose_scoped import compose_scoped_layout

logger = logging.getLogger("whiskers.plugins.portfolio.intent_compose")


def default_fragment_page(audience: str = "default") -> list[dict[str, Any]]:
    """Deprecated compatibility page ids (fragments removed; not executed).

    Kept so existing unit tests / callers that only inspect the default page
    shape still see a stable audience-only matrix, without keyword routing.
    """
    page: list[dict[str, Any]] = [
        {"fragment": "hero.full"},
        {"fragment": "proof.kpiGrid"},
        {"fragment": "work.cards"},
        {"fragment": "story.star"},
        {"fragment": "cta.quickActions"},
    ]
    if audience == "peer":
        page.insert(-1, {"fragment": "system.arch"})
    return page


def pick_fragments_for_intent(query: str, audience: str) -> list[dict[str, Any]]:
    """Deprecated: audience-only default page (regex routing removed)."""
    _ = query
    return default_fragment_page(audience)


async def compose_intent_layout(
    user_query: str,
    *,
    tenant_id: int = 1,
    theme: str = "",
    refresh: bool = True,
    page: list[dict[str, Any]] | None = None,
    use_fragments: bool = False,
    top_k: int = 3,
) -> dict[str, Any]:
    """Compose a live layout for Ask/query intent.

    Returns ``{status, layout, audience, star_query, mode, fragments?}``.

    - Default: scoped GenUI path (``mode: scoped``).
    - Explicit ``page`` / ``use_fragments``: floor composer (fragments gone).
    - On failure: audience template (``mode: template``).
    """
    if not user_query or not str(user_query).strip():
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["user_query"],
        }

    # Fragments removed — explicit page/use_fragments go through floor composer.
    if (isinstance(page, list) and page) or use_fragments:
        try:
            from plugins.portfolio_plugin.compose.floor import build_floor_layout

            audience, star_query = infer_audience_from_job_signals(user_query)
            audience_n, template = get_audience_template(audience)
            if not star_query:
                star_query = template.get("star_query")
            layout = await build_floor_layout(
                user_query,
                tenant_id=int(tenant_id),
                audience=audience_n,
                theme=theme or "",
                refresh=refresh,
            )
            if isinstance(layout, dict) and layout.get("blocks"):
                return {
                    "status": "ok",
                    "layout": layout,
                    "audience": audience_n,
                    "star_query": star_query,
                    "mode": "floor",
                    "fragments": [],
                }
        except Exception as exc:
            logger.warning("intent compose: floor fail-open: %s", exc)

    try:
        return await compose_scoped_layout(
            user_query,
            tenant_id=tenant_id,
            theme=theme,
            refresh=refresh,
            top_k=top_k,
        )
    except Exception as exc:
        logger.warning("intent compose: scoped fail-open: %s", exc)

    audience, star_query = infer_audience_from_job_signals(user_query)
    audience_n, template = get_audience_template(audience)
    if not star_query:
        star_query = template.get("star_query")
    layout = await compose_layout(
        audience=audience_n,
        star_query=star_query,
        tenant_id=tenant_id,
        refresh=refresh,
    )
    if theme and str(theme).strip() and isinstance(layout, dict):
        meta = dict(layout.get("meta") or {})
        meta["theme"] = str(theme).strip()
        meta["mode"] = "template"
        layout = {**layout, "meta": meta}
    return {
        "status": "ok",
        "layout": layout,
        "audience": audience_n,
        "star_query": star_query,
        "mode": "template",
        "fragments": [],
    }
