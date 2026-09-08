"""
Database persistence store for the Job Search plugin.

Handles profile CRUD, job application tracking, and preference vector search.
"""

import logging
from datetime import datetime, timezone
from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db_layer.connection import get_async_session
from db_layer.embeddings.search_engine import SearchSpec
from db_layer.embeddings.search_engine import search as search_engine_search
from plugins.job_search_plugin.models import (
    JobApplicantProfile,
    JobApplication,
    JobPostingLiveness,
    JobPreferenceEmbedding,
)

logger = logging.getLogger("whiskers")


def _profile_to_dict(p: JobApplicantProfile) -> dict:
    """Serialize a JobApplicantProfile ORM instance to a dictionary."""
    return {
        "id": p.id,
        "full_name": p.full_name,
        "email": p.email,
        "phone": p.phone,
        "base_resume_text": p.base_resume_text,
        "cover_letter_template": p.cover_letter_template,
        "resume_object_key": p.resume_object_key,
        "preferences_text": p.preferences_text,
        "location": p.location,
        "target_titles": p.target_titles if p.target_titles is not None else [],
        "top_skills": p.top_skills if p.top_skills is not None else [],
        "constraints_text": p.constraints_text,
        "notice_period_days": p.notice_period_days,
        "core_technologies": p.core_technologies if p.core_technologies is not None else [],
        "methodologies": p.methodologies if p.methodologies is not None else [],
        "languages": p.languages if p.languages is not None else [],
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _app_to_dict(a: JobApplication) -> dict:
    """Serialize a JobApplication ORM instance to a dictionary."""
    return {
        "id": a.id,
        "applicant_profile_id": a.applicant_profile_id,
        "job_id": a.job_id,
        "provider": a.provider,
        "provider_application_id": a.provider_application_id,
        "status": a.status,
        "notes": a.notes,
        "resume_object_key": a.resume_object_key,
        "cover_letter_object_key": a.cover_letter_object_key,
        "portfolio_job_id": a.portfolio_job_id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


async def create_profile(**fields) -> dict:
    """Create a new JobApplicantProfile."""
    async with get_async_session() as session:
        profile = JobApplicantProfile(**fields)
        session.add(profile)
        await session.flush()
        result = _profile_to_dict(profile)
        await session.commit()
        return result


async def get_profile(profile_id: int) -> dict | None:
    """Get a JobApplicantProfile by its ID."""
    async with get_async_session() as session:
        stmt = select(JobApplicantProfile).where(JobApplicantProfile.id == profile_id)
        result = await session.execute(stmt)
        profile = result.scalar_one_or_none()
        if not profile:
            return None
        return _profile_to_dict(profile)


async def get_profile_scoped(profile_id: int, tenant_id: int | None = None) -> dict | None:
    """Get a JobApplicantProfile by ID, scoped to the resolved tenant.

    Explicit tenant_id wins; otherwise current_tenant_id contextvar (default 1).
    Unlike get_profile, this never returns a row belonging to another tenant.
    """
    from core.context import current_tenant_id as _tenant_cv

    resolved_tenant = tenant_id if tenant_id is not None else _tenant_cv.get()
    if resolved_tenant is None:
        resolved_tenant = 1  # fail closed — never unscoped cross-tenant

    async with get_async_session() as session:
        stmt = select(JobApplicantProfile).where(
            JobApplicantProfile.id == profile_id,
            JobApplicantProfile.tenant_id == resolved_tenant,
        )
        result = await session.execute(stmt)
        profile = result.scalar_one_or_none()
        if not profile:
            return None
        return _profile_to_dict(profile)


async def upsert_profile(profile_id: int | None, **fields) -> dict:
    """Upsert a JobApplicantProfile.

    If profile_id is None, creates a new profile.
    Uses PostgreSQL insert ON CONFLICT to upsert if profile_id is provided.
    """
    if profile_id is None:
        return await create_profile(**fields)

    async with get_async_session() as session:
        # Check if it exists or use pg_insert
        stmt = (
            pg_insert(JobApplicantProfile)
            .values(id=profile_id, **fields)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={**fields, "updated_at": func.now()},
            )
        )
        await session.execute(stmt)
        await session.commit()

    # Retrieve and return the upserted profile
    updated = await get_profile(profile_id)
    if not updated:
        raise ValueError(f"Failed to upsert/fetch profile with ID {profile_id}")
    return updated


async def create_application(applicant_profile_id: int, job_id: str, provider: str, **fields) -> dict:
    """Create a new JobApplication, defaulting status to 'drafted'."""
    async with get_async_session() as session:
        status = fields.pop("status", "drafted")
        # Stamp tenant so public agent-status queries remain tenant-scoped.
        if "tenant_id" not in fields or fields.get("tenant_id") is None:
            from core.context import current_tenant_id as _tenant_cv
            fields["tenant_id"] = _tenant_cv.get() or 1
        app = JobApplication(
            applicant_profile_id=applicant_profile_id,
            job_id=job_id,
            provider=provider,
            status=status,
            **fields
        )
        session.add(app)
        await session.flush()
        result = _app_to_dict(app)
        await session.commit()
        return result


async def get_application(application_id: int) -> dict | None:
    """Get a JobApplication by its ID."""
    async with get_async_session() as session:
        stmt = select(JobApplication).where(JobApplication.id == application_id)
        result = await session.execute(stmt)
        app = result.scalar_one_or_none()
        if not app:
            return None
        return _app_to_dict(app)


async def update_application(application_id: int, **fields) -> dict | None:
    """Update a JobApplication with only the provided fields.

    Only provided keys in fields are touched. Returns the updated application dictionary.
    """
    allowed_keys = {
        "status",
        "notes",
        "provider_application_id",
        "resume_object_key",
        "cover_letter_object_key",
        "portfolio_job_id",
    }
    update_values = {k: v for k, v in fields.items() if k in allowed_keys}
    if not update_values:
        return await get_application(application_id)

    async with get_async_session() as session:
        stmt = select(JobApplication).where(JobApplication.id == application_id)
        res = await session.execute(stmt)
        app = res.scalar_one_or_none()
        if not app:
            return None
        for k, v in update_values.items():
            setattr(app, k, v)
        app.updated_at = datetime.now(timezone.utc)
        await session.flush()
        result = _app_to_dict(app)
        await session.commit()
        return result


async def list_applications(applicant_profile_id: int | None = None, status: str | None = None) -> list[dict]:
    """List JobApplications, optionally filtering by applicant profile ID or status."""
    async with get_async_session() as session:
        stmt = select(JobApplication)
        if applicant_profile_id is not None:
            stmt = stmt.where(JobApplication.applicant_profile_id == applicant_profile_id)
        if status is not None:
            stmt = stmt.where(JobApplication.status == status)
        stmt = stmt.order_by(JobApplication.created_at.desc())
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [_app_to_dict(r) for r in rows]


async def get_latest_agent_status(
    job_id: str | None = None,
    portfolio_job_id: str | None = None,
    tenant_id: int | None = None,
) -> dict | None:
    """Privacy-safe public status snapshot of the most recent job application.

    Returns only {job_id, status, updated_at} — never applicant PII (name,
    email, notes, resume keys) since this backs a public portfolio widget.
    Filters by job_id or portfolio_job_id when given; otherwise returns the
    most recently updated application for the resolved tenant.
    Tenant is required: explicit tenant_id wins; otherwise current_tenant_id
    contextvar (default 1 on unauthenticated PUBLIC paths).
    """
    from core.context import current_tenant_id as _tenant_cv

    resolved_tenant = tenant_id if tenant_id is not None else _tenant_cv.get()
    if resolved_tenant is None:
        resolved_tenant = 1  # fail closed — never unscoped cross-tenant

    async with get_async_session() as session:
        stmt = select(JobApplication).where(JobApplication.tenant_id == resolved_tenant)
        if job_id is not None:
            stmt = stmt.where(JobApplication.job_id == job_id)
        if portfolio_job_id is not None:
            stmt = stmt.where(JobApplication.portfolio_job_id == portfolio_job_id)
        stmt = stmt.order_by(JobApplication.updated_at.desc()).limit(1)
        result = await session.execute(stmt)
        app = result.scalar_one_or_none()
        if not app:
            return None
        return {
            "job_id": app.job_id,
            "status": app.status,
            "updated_at": app.updated_at.isoformat() if app.updated_at else None,
        }


_JOB_PREF_SEARCH_SPEC = SearchSpec(
    name="job_preferences",
    model=JobPreferenceEmbedding,
    select_cols=(
        JobPreferenceEmbedding.content,
        JobPreferenceEmbedding.source,
        JobPreferenceEmbedding.applicant_profile_id,
    ),
    embedding_col=JobPreferenceEmbedding.embedding,
    model_col=JobPreferenceEmbedding.model,
    search_doc_col=JobPreferenceEmbedding.search_doc,
    key_fn=lambda row: (row["applicant_profile_id"], row["source"], row["content"]),
    row_to_dict=lambda row: {
        "content": row.content,
        "source": row.source,
        "applicant_profile_id": row.applicant_profile_id,
    },
)


async def search_preferences_hybrid(
    applicant_profile_id: int,
    query_text: str,
    top_k: int = 8,
) -> list[dict]:
    """Hybrid (RRF dense+sparse) search over one applicant's preference/resume chunks.

    Thin adapter over db_layer/embeddings/search_engine.py — the single choke point for
    hybrid-vs-dense decisions. Do not hand-roll fusion here.
    """
    from core.llm_config_service import resolve_tool_embedding

    sel = await resolve_tool_embedding("job_search_plugin", "index_preferences")

    def _extra_filters(stmt):
        return stmt.where(JobPreferenceEmbedding.applicant_profile_id == applicant_profile_id)

    return await search_engine_search(
        _JOB_PREF_SEARCH_SPEC, query_text, sel, top_k, extra_filters=_extra_filters
    )


async def search_preferences(applicant_profile_id: int, query_vector: list[float], top_k: int = 5) -> list[dict]:
    """Perform cosine distance similarity search on preference embeddings."""
    async with get_async_session() as session:
        stmt = (
            select(
                JobPreferenceEmbedding.content,
                JobPreferenceEmbedding.source,
                (1 - JobPreferenceEmbedding.embedding.cosine_distance(query_vector)).label("similarity"),
            )
            .where(JobPreferenceEmbedding.applicant_profile_id == applicant_profile_id)
            .where(JobPreferenceEmbedding.embedding.is_not(None))
            .order_by(JobPreferenceEmbedding.embedding.cosine_distance(query_vector))
            .limit(top_k)
        )
        result = await session.execute(stmt)
        return [
            {
                "content": row.content,
                "source": row.source,
                "similarity": float(row.similarity),
            }
            for row in result.all()
        ]


async def add_preference_embedding(
    applicant_profile_id: int,
    source: str,
    content: str,
    embedding: list[float],
    model: str = "",
) -> dict:
    """Insert or upsert a preference/resume chunk embedding into the database."""
    import hashlib
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    async with get_async_session() as session:
        stmt = (
            pg_insert(JobPreferenceEmbedding)
            .values(
                applicant_profile_id=applicant_profile_id,
                source=source,
                content=content,
                embedding=embedding,
                model=model,
                content_hash=content_hash,
            )
            .on_conflict_do_update(
                index_elements=["applicant_profile_id", "source", "content_hash"],
                set_={
                    "embedding": embedding,
                    "model": model,
                    "synced_at": func.now(),
                },
            )
        )
        await session.execute(stmt)
        await session.commit()

    return {
        "applicant_profile_id": applicant_profile_id,
        "source": source,
        "content_hash": content_hash,
    }


# A re-verification after this many days since last_verified_at counts as a
# repost occurrence rather than a plain freshness refresh.
REPOST_GAP_DAYS = 14


def _hash_url(url: str) -> str:
    """SHA-256 hex digest of a posting URL — the job_posting_liveness dedupe key."""
    import hashlib
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _liveness_to_dict(row: JobPostingLiveness) -> dict:
    """Serialize a JobPostingLiveness ORM instance to a dictionary."""
    return {
        "id": row.id,
        "url": row.url,
        "company": row.company,
        "role_title": row.role_title,
        "provider": row.provider,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "is_active": row.is_active,
        "repost_count": row.repost_count,
        "staleness_days": row.staleness_days,
        "ghost_signals": row.ghost_signals if row.ghost_signals is not None else {},
    }


async def upsert_liveness(
    url: str,
    company: str = "",
    role_title: str = "",
    provider: str = "",
    is_live: bool = True,
    tenant_id: int | None = None,
    now: datetime | None = None,
) -> tuple[dict, bool]:
    """Create-or-refresh a job_posting_liveness row keyed on sha256(url).

    A re-check more than REPOST_GAP_DAYS after the prior last_verified_at
    increments repost_count (a repost signal); a closer-together re-check is
    treated as a plain freshness refresh. Returns (record_dict, created).
    `now` is an injectable clock seam for deterministic tests.
    """
    from core.context import current_tenant_id as _tenant_cv

    resolved_tenant = tenant_id if tenant_id is not None else (_tenant_cv.get() or 1)
    resolved_now = now if now is not None else datetime.now(timezone.utc)
    url_hash = _hash_url(url)

    async with get_async_session() as session:
        stmt = (
            select(JobPostingLiveness)
            .where(JobPostingLiveness.url_hash == url_hash)
            .with_for_update()
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            row = JobPostingLiveness(
                url_hash=url_hash,
                url=url,
                company=company,
                role_title=role_title,
                provider=provider or None,
                first_seen_at=resolved_now,
                last_verified_at=resolved_now,
                is_active=is_live,
                repost_count=1,
                staleness_days=0,
                tenant_id=resolved_tenant,
            )
            session.add(row)
            await session.flush()
            result_dict = _liveness_to_dict(row)
            await session.commit()
            return result_dict, True

        gap_days = (resolved_now - row.last_verified_at).days if row.last_verified_at else 0
        if gap_days >= REPOST_GAP_DAYS:
            row.repost_count += 1
        row.last_verified_at = resolved_now
        row.staleness_days = (resolved_now - row.first_seen_at).days if row.first_seen_at else 0
        row.is_active = is_live
        if company:
            row.company = company
        if role_title:
            row.role_title = role_title
        if provider:
            row.provider = provider
        await session.flush()
        result_dict = _liveness_to_dict(row)
        await session.commit()
        return result_dict, False


async def get_liveness(url: str, tenant_id: int | None = None) -> dict | None:
    """Look up an existing job_posting_liveness row by URL, scoped to tenant."""
    from core.context import current_tenant_id as _tenant_cv

    resolved_tenant = tenant_id if tenant_id is not None else (_tenant_cv.get() or 1)
    url_hash = _hash_url(url)

    async with get_async_session() as session:
        stmt = select(JobPostingLiveness).where(
            JobPostingLiveness.url_hash == url_hash,
            JobPostingLiveness.tenant_id == resolved_tenant,
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return _liveness_to_dict(row)


_ALLOWED_SYNC_FIELDS = {"notes", "portfolio_job_id"}


async def upsert_application_status(
    applicant_profile_id: int,
    job_id: str,
    status: str,
    provider: str = "",
    **fields,
) -> tuple[dict, bool]:
    """Idempotently create-or-update a JobApplication keyed on (tenant, profile, job_id).

    No DB-level unique constraint exists on (applicant_profile_id, job_id) — a prior
    plugin version could have created duplicate rows, and a real UNIQUE index migration
    risks failing closed on unclean prod data. Race-safety instead comes from a
    Postgres advisory xact lock scoped to this key, held for the read-then-write inside
    one transaction. Returns (application_dict, created).
    """
    from core.context import current_tenant_id as _tenant_cv

    resolved_tenant = _tenant_cv.get() or 1
    lock_key = f"job_search_plugin:app_status:{resolved_tenant}:{applicant_profile_id}:{job_id}"

    update_values = {k: v for k, v in fields.items() if k in _ALLOWED_SYNC_FIELDS and v}
    update_values["status"] = status

    async with get_async_session() as session:
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))

        stmt = (
            select(JobApplication)
            .where(
                JobApplication.applicant_profile_id == applicant_profile_id,
                JobApplication.job_id == job_id,
                JobApplication.tenant_id == resolved_tenant,
            )
            .with_for_update()
        )
        result = await session.execute(stmt)
        app = result.scalar_one_or_none()

        if app is not None:
            for k, v in update_values.items():
                setattr(app, k, v)
            app.updated_at = datetime.now(timezone.utc)
            await session.flush()
            result_dict = _app_to_dict(app)
            await session.commit()
            return result_dict, False

        new_app = JobApplication(
            applicant_profile_id=applicant_profile_id,
            job_id=job_id,
            provider=provider,
            tenant_id=resolved_tenant,
            **update_values,
        )
        session.add(new_app)
        await session.flush()
        result_dict = _app_to_dict(new_app)
        await session.commit()
        return result_dict, True
