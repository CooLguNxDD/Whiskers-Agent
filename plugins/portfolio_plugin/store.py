"""Database persistence store for the Portfolio plugin.

Handles portfolio project CRUD operations.

**Single-tenant assumption:** public / default portfolio reads use
``PORTFOLIO_TENANT_ID``. Write paths that accept an authenticated principal
should pass that tenant explicitly rather than inventing one.
"""

import logging
from datetime import date
from sqlalchemy import select, delete
from db_layer.connection import get_async_session
from plugins.portfolio_plugin.models import (
    ASK_TURN_INTENT_MAX,
    ASK_TURN_PARENT_RUN_ID_MAX,
    ASK_TURN_QUESTION_MAX,
    ASK_TURN_RUN_ID_MAX,
    ASK_TURN_SESSION_MAX,
    ASK_TURN_SLUG_MAX,
    ASK_TURN_SUBJECT_MAX,
    ASK_TURN_VIEW_MAX,
    PortfolioAskTurn,
    PortfolioBakeRun,
    PortfolioProject,
    PortfolioJobLayout,
)

# Upper bound for list_ask_turns / list_bake_runs style debug reads — the
# existing list_bake_runs only clamps the low side; cap the high side here so
# an operator/UI can't request an unbounded scan.
_MAX_LIST_LIMIT = 500

logger = logging.getLogger("whiskers.plugins.portfolio.store")

# Greppable single-tenant default (public surfaces always tenant 1).
PORTFOLIO_TENANT_ID = 1


def _clip(value: str | None, width: int) -> str | None:
    """Truncate a visitor-supplied string to a VARCHAR width; None stays None."""
    if value is None:
        return None
    return value[:width]


def _project_to_dict(p: PortfolioProject) -> dict:
    """Serialize a PortfolioProject ORM instance to a dictionary."""
    return {
        "id": p.id,
        "slug": p.slug,
        "name": p.name,
        "summary": p.summary,
        "tags": p.tags,
        "metrics": p.metrics,
        "links": p.links,
        "audiences": p.audiences,
        "sort_order": p.sort_order,
        "is_active": p.is_active,
        "context_sources": p.context_sources if p.context_sources is not None else [],
        "started_on": p.started_on.isoformat() if p.started_on else None,
        "ended_on": p.ended_on.isoformat() if p.ended_on else None,
        "timeline_source": p.timeline_source,
        "tenant_id": p.tenant_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


async def list_projects(
    audience: str | None = None,
    include_inactive: bool = False,
    tenant_id: int = PORTFOLIO_TENANT_ID,
) -> list[dict]:
    """List portfolio projects with optional audience, status, and tenant filters."""
    async with get_async_session() as session:
        stmt = select(PortfolioProject).where(PortfolioProject.tenant_id == tenant_id)
        if not include_inactive:
            stmt = stmt.where(PortfolioProject.is_active == True)
        stmt = stmt.order_by(PortfolioProject.sort_order, PortfolioProject.id)
        result = await session.execute(stmt)
        rows = result.scalars().all()

        if audience:
            rows = [
                row for row in rows
                if not row.audiences or audience in row.audiences
            ]

        return [_project_to_dict(r) for r in rows]


async def get_project(slug: str, tenant_id: int = PORTFOLIO_TENANT_ID) -> dict | None:
    """Retrieve a single portfolio project by slug, scoped to tenant."""
    async with get_async_session() as session:
        stmt = (
            select(PortfolioProject)
            .where(PortfolioProject.slug == slug)
            .where(PortfolioProject.tenant_id == tenant_id)
        )
        result = await session.execute(stmt)
        project = result.scalar_one_or_none()
        if not project:
            return None
        return _project_to_dict(project)


async def upsert_project(slug: str, tenant_id: int, **fields) -> dict:
    """Upsert a portfolio project by slug, scoped to tenant, updating only allowed fields."""
    allowed_keys = {
        "name",
        "summary",
        "tags",
        "metrics",
        "links",
        "audiences",
        "sort_order",
        "is_active",
        "context_sources",
        "started_on",
        "ended_on",
        "timeline_source",
    }
    update_values = {k: v for k, v in fields.items() if k in allowed_keys}
    # Date columns need python date objects, not ISO strings, for asyncpg.
    for date_key in ("started_on", "ended_on"):
        raw = update_values.get(date_key)
        if isinstance(raw, str):
            if not raw:
                update_values[date_key] = None
                continue
            try:
                update_values[date_key] = date.fromisoformat(raw[:10])
            except ValueError:
                logger.warning(
                    "upsert_project: malformed %s %r for slug=%s, dropping", date_key, raw, slug
                )
                update_values[date_key] = None

    async with get_async_session() as session:
        stmt = (
            select(PortfolioProject)
            .where(PortfolioProject.slug == slug)
            .where(PortfolioProject.tenant_id == tenant_id)
        )
        res = await session.execute(stmt)
        project = res.scalar_one_or_none()

        if project:
            for k, v in update_values.items():
                setattr(project, k, v)
        else:
            project = PortfolioProject(slug=slug, tenant_id=tenant_id, **update_values)
            session.add(project)

        await session.flush()
        # onupdate timestamps expire attrs after flush; refresh before serialize
        await session.refresh(project)
        result = _project_to_dict(project)
        await session.commit()
        return result


async def delete_project(slug: str, tenant_id: int) -> bool:
    """Delete a portfolio project by slug, scoped to tenant."""
    async with get_async_session() as session:
        stmt = (
            delete(PortfolioProject)
            .where(PortfolioProject.slug == slug)
            .where(PortfolioProject.tenant_id == tenant_id)
        )
        res = await session.execute(stmt)
        await session.commit()
        return (res.rowcount or 0) > 0


def _job_layout_to_dict(row: PortfolioJobLayout) -> dict:
    """Serialize a PortfolioJobLayout ORM instance to a dictionary."""
    return {
        "id": row.id,
        "short_id": row.short_id,
        "job_application_job_id": row.job_application_job_id,
        "tenant_id": row.tenant_id,
        "audience": row.audience,
        "star_query": row.star_query,
        "layout_json": row.layout_json,
        "plan_json": getattr(row, "plan_json", None),
        "recipe_id": getattr(row, "recipe_id", None),
        "jury_score": getattr(row, "jury_score", None),
        "parent_short_id": getattr(row, "parent_short_id", None),
        "is_derived": bool(getattr(row, "is_derived", False)),
        "compose_path": getattr(row, "compose_path", None),
        "mode": getattr(row, "mode", None),
        "degraded": bool(getattr(row, "degraded", False)),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": (
            row.updated_at.isoformat() if getattr(row, "updated_at", None) else None
        ),
    }


async def job_layout_short_id_exists(short_id: str) -> bool:
    """Check whether a job layout short_id is already taken."""
    async with get_async_session() as session:
        stmt = select(PortfolioJobLayout.id).where(PortfolioJobLayout.short_id == short_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def create_job_layout(
    short_id: str,
    layout: dict,
    audience: str,
    *,
    tenant_id: int,
    star_query: str | None = None,
    job_application_job_id: str | None = None,
    plan_json: dict | None = None,
    recipe_id: str | None = None,
    jury_score: float | None = None,
    parent_short_id: str | None = None,
    is_derived: bool = False,
    compose_path: str | None = None,
    mode: str | None = None,
    degraded: bool = False,
) -> dict:
    """Persist a job-specific baked layout artifact keyed by short_id.

    ``plan_json``/``recipe_id``/``jury_score`` are durable provenance -- the
    queryable ground truth for "what did we ship for this job", alongside the
    fuzzy vector index in ``portfolio_plugin__layouts``.
    ``compose_path``/``mode``/``degraded`` answer "which rung shipped it".

    Chat patches set ``is_derived=True`` + ``parent_short_id`` so the original
    HR-facing bake stays immutable.
    """
    async with get_async_session() as session:
        row = PortfolioJobLayout(
            short_id=short_id,
            layout_json=layout,
            audience=audience,
            star_query=star_query,
            job_application_job_id=job_application_job_id,
            tenant_id=tenant_id,
            plan_json=plan_json,
            recipe_id=recipe_id,
            jury_score=jury_score,
            parent_short_id=parent_short_id,
            is_derived=bool(is_derived),
            compose_path=compose_path,
            mode=mode,
            degraded=bool(degraded),
        )
        session.add(row)
        await session.flush()
        result = _job_layout_to_dict(row)
        await session.commit()
        return result


async def get_job_layout_by_short_id(short_id: str) -> dict | None:
    """Retrieve a baked job layout artifact by its short_id."""
    async with get_async_session() as session:
        stmt = select(PortfolioJobLayout).where(PortfolioJobLayout.short_id == short_id)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if not row:
            return None
        return _job_layout_to_dict(row)


async def create_bake_run(
    *,
    run_id: str,
    tenant_id: int,
    status: str,
    short_id: str | None = None,
    compose_path: str | None = None,
    mode: str | None = None,
    degraded: bool = False,
    company: str | None = None,
    role: str | None = None,
    job_brief_hash: str | None = None,
    evidence_pack_hash: str | None = None,
    stages: dict | None = None,
    errors: list | None = None,
    total_ms: int | None = None,
) -> str | None:
    """Record one bake attempt, successful or not.

    Written unconditionally — a bake that shipped nothing leaves ``short_id``
    NULL, which is the only durable trace that the attempt happened at all.
    Never raises: observability must not be able to fail a bake.
    """
    try:
        async with get_async_session() as session:
            row = PortfolioBakeRun(
                run_id=run_id,
                tenant_id=int(tenant_id),
                short_id=short_id,
                status=status,
                compose_path=compose_path,
                mode=mode,
                degraded=bool(degraded),
                company=company,
                role=role,
                job_brief_hash=job_brief_hash,
                evidence_pack_hash=evidence_pack_hash,
                stages_json=stages or {},
                errors_json=errors or [],
                total_ms=int(total_ms) if total_ms is not None else None,
            )
            session.add(row)
            await session.commit()
            return run_id
    except Exception as exc:
        logger.warning("bake run record failed for run_id=%s: %s", run_id, exc)
        return None


async def list_bake_runs(
    *,
    tenant_id: int,
    limit: int = 50,
    degraded_only: bool = False,
) -> list[dict]:
    """Recent bake attempts for a tenant, newest first. Debug surface."""
    async with get_async_session() as session:
        stmt = select(PortfolioBakeRun).where(PortfolioBakeRun.tenant_id == int(tenant_id))
        if degraded_only:
            stmt = stmt.where(PortfolioBakeRun.degraded.is_(True))
        stmt = stmt.order_by(PortfolioBakeRun.created_at.desc()).limit(max(1, int(limit)))
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "run_id": r.run_id,
                "short_id": r.short_id,
                "status": r.status,
                "compose_path": r.compose_path,
                "mode": r.mode,
                "degraded": bool(r.degraded),
                "company": r.company,
                "role": r.role,
                "total_ms": r.total_ms,
                "stages": r.stages_json,
                "errors": r.errors_json,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


async def create_ask_turn(
    *,
    run_id: str,
    tenant_id: int,
    question: str,
    subject: str | None = None,
    visitor_session_id: str | None = None,
    intent: str | None = None,
    view: str | None = None,
    focus_slug: str | None = None,
    highlight_slugs: list | None = None,
    add_slugs: list | None = None,
    ok: bool = True,
    error_type: str | None = None,
    latency_ms: int | None = None,
    parent_run_id: str | None = None,
) -> str | None:
    """Record one fish-tank visitor ask turn. Never raises — mirrors create_bake_run.

    Question text is truncated to ASK_TURN_QUESTION_MAX; id/subject/intent/view
    fields are clipped to their VARCHAR widths so an over-long visitor value
    cannot drop the whole audit row. The overlay itself is never persisted
    (ask stays ephemeral — see ask/overlay.py module docstring).
    """
    try:
        async with get_async_session() as session:
            row = PortfolioAskTurn(
                run_id=_clip(run_id, ASK_TURN_RUN_ID_MAX) or "",
                tenant_id=int(tenant_id),
                subject=_clip(subject, ASK_TURN_SUBJECT_MAX),
                visitor_session_id=_clip(visitor_session_id, ASK_TURN_SESSION_MAX),
                question=(question or "")[:ASK_TURN_QUESTION_MAX],
                intent=_clip(intent, ASK_TURN_INTENT_MAX),
                view=_clip(view, ASK_TURN_VIEW_MAX),
                focus_slug=_clip(focus_slug, ASK_TURN_SLUG_MAX),
                highlight_slugs=highlight_slugs or [],
                add_slugs=add_slugs or [],
                ok=bool(ok),
                error_type=error_type,
                latency_ms=int(latency_ms) if latency_ms is not None else None,
                parent_run_id=_clip(parent_run_id, ASK_TURN_PARENT_RUN_ID_MAX),
            )
            session.add(row)
            await session.commit()
            return run_id
    except Exception as exc:
        logger.warning("ask turn record failed for run_id=%s: %s", run_id, exc)
        return None


async def mark_ask_turn_failed(*, run_id: str, error_type: str | None = None) -> None:
    """Idempotent error-path update: flip an existing ask turn to ok=False in place.

    Used when the overlay build fails after route_ask already recorded a
    successful turn — one row per run_id, never a second write.
    """
    try:
        async with get_async_session() as session:
            row = (
                await session.execute(
                    select(PortfolioAskTurn).where(PortfolioAskTurn.run_id == run_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.ok = False
            row.error_type = error_type
            await session.commit()
    except Exception as exc:
        logger.warning("ask turn error-mark failed for run_id=%s: %s", run_id, exc)


async def list_ask_turns(
    *,
    tenant_id: int,
    limit: int = 50,
    intent: str | None = None,
) -> list[dict]:
    """Recent ask turns for a tenant, newest first. Debug/admin surface."""
    async with get_async_session() as session:
        stmt = select(PortfolioAskTurn).where(PortfolioAskTurn.tenant_id == int(tenant_id))
        if intent:
            stmt = stmt.where(PortfolioAskTurn.intent == intent)
        stmt = stmt.order_by(PortfolioAskTurn.created_at.desc()).limit(
            max(1, min(int(limit), _MAX_LIST_LIMIT))
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [
            {
                "run_id": r.run_id,
                "subject": r.subject,
                "visitor_session_id": r.visitor_session_id,
                "question": r.question,
                "intent": r.intent,
                "view": r.view,
                "focus_slug": r.focus_slug,
                "highlight_slugs": r.highlight_slugs,
                "add_slugs": r.add_slugs,
                "ok": bool(r.ok),
                "error_type": r.error_type,
                "latency_ms": r.latency_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


async def update_job_layout(
    short_id: str,
    *,
    layout: dict,
    tenant_id: int,
) -> bool:
    """Update a **derived** job layout's JSON. Fails closed on non-derived rows.

    Returns True only when a matching derived row (same tenant when provided)
    was updated. Refuses missing rows, tenant mismatch, and original bakes
    (``is_derived is False``) so shared ``?j=`` HR links stay immutable.
    """
    if not short_id or not str(short_id).strip():
        return False
    if not isinstance(layout, dict):
        return False
    async with get_async_session() as session:
        stmt = select(PortfolioJobLayout).where(
            PortfolioJobLayout.short_id == str(short_id).strip()
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False
        if not bool(getattr(row, "is_derived", False)):
            return False
        if row.tenant_id is not None:
            try:
                if int(row.tenant_id) != int(tenant_id):
                    return False
            except (TypeError, ValueError):
                logger.warning(
                    "update_job_layout: tenant id cast failed row.tenant_id=%r tenant_id=%r short_id=%s",
                    row.tenant_id, tenant_id, short_id,
                )
                return False
        row.layout_json = layout
        await session.commit()
        return True
