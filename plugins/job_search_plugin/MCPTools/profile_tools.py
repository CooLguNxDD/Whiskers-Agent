"""
MCP Tools for managing job applicant profiles.
"""

import logging
from core.context import mcp
from plugins.job_search_plugin import store

logger = logging.getLogger("whiskers.plugins")

_VALID_TIERS = {"compact", "skills_only", "full"}


@mcp.tool(
    title="get_applicant_profile",
    tags={"job_search_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_applicant_profile(applicant_profile_id: int) -> dict:
    """Get the applicant profile by ID.

    Delegates to store.get_profile. Returns error dict if not found.
    """
    profile = await store.get_profile(applicant_profile_id)
    if profile is None:
        return {"status": "error", "error": "profile_not_found"}
    return profile


@mcp.tool(
    title="upsert_applicant_profile",
    tags={"job_search_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def upsert_applicant_profile(
    applicant_profile_id: int | None = None,
    full_name: str = "",
    email: str = "",
    phone: str = "",
    base_resume_text: str = "",
    cover_letter_template: str = "",
    preferences_text: str = "",
    location: str = "",
    target_titles: list[str] | None = None,
    top_skills: list[str] | None = None,
    constraints_text: str = "",
    notice_period_days: int | None = None,
    core_technologies: list[str] | None = None,
    methodologies: list[str] | None = None,
    languages: list[str] | None = None,
) -> dict:
    """Upsert applicant profile fields, including compact/skills_only tier targeting data.

    Validates that full_name and email are provided if applicant_profile_id is None.
    Delegates to store.upsert_profile.
    """
    if applicant_profile_id is None:
        missing = []
        if not full_name:
            missing.append("full_name")
        if not email:
            missing.append("email")
        if missing:
            return {
                "status": "error",
                "error": "missing_required_fields",
                "missing_fields": missing,
                "message": "Please provide: " + "; ".join(missing),
            }

    profile = await store.upsert_profile(
        applicant_profile_id,
        full_name=full_name,
        email=email,
        phone=phone,
        base_resume_text=base_resume_text,
        cover_letter_template=cover_letter_template,
        preferences_text=preferences_text,
        location=location,
        target_titles=target_titles if target_titles is not None else [],
        top_skills=top_skills if top_skills is not None else [],
        constraints_text=constraints_text,
        notice_period_days=notice_period_days,
        core_technologies=core_technologies if core_technologies is not None else [],
        methodologies=methodologies if methodologies is not None else [],
        languages=languages if languages is not None else [],
    )
    return profile


@mcp.tool(
    title="get_applicant_profile_summary",
    tags={"job_search_plugin", "profile", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_applicant_profile_summary(
    applicant_profile_id: int,
    tier: str = "compact",
) -> dict:
    """Returns a token-budgeted profile summary for AI CLI agents (Career-Ops).

    tier="compact" (~1000 chars, default): id/full_name/email/location/target_titles/
    top_skills/constraints_text/notice_period_days.
    tier="skills_only": id/full_name/core_technologies/methodologies/languages.
    tier="full": the whole profile record (base_resume_text, preferences_text included).
    Unknown tier fails loud with invalid_tier rather than silently falling back — this
    is a public tool contract for an external client.
    """
    if tier not in _VALID_TIERS:
        return {"status": "error", "error": "invalid_tier", "valid_tiers": sorted(_VALID_TIERS)}

    profile = await store.get_profile(applicant_profile_id)
    if profile is None:
        return {"status": "error", "error": "profile_not_found"}

    if tier == "full":
        return profile

    if tier == "skills_only":
        return {
            "id": profile["id"],
            "full_name": profile["full_name"],
            "core_technologies": profile["core_technologies"],
            "methodologies": profile["methodologies"],
            "languages": profile["languages"],
        }

    # compact
    return {
        "id": profile["id"],
        "full_name": profile["full_name"],
        "email": profile["email"],
        "location": profile["location"],
        "target_titles": profile["target_titles"],
        "top_skills": profile["top_skills"],
        "constraints_text": profile["constraints_text"],
        "notice_period_days": profile["notice_period_days"],
    }


async def _retrieve_chunks(applicant_profile_id: int, query_text: str, top_k: int) -> list[dict]:
    """Retrieve resume/preference chunks for a query string.

    Hybrid (dense+sparse RRF) via store.search_preferences_hybrid — the Stage 4
    adapter over db_layer/embeddings/search_engine.py, the single hybrid-search
    choke point. Isolated behind this function so callers don't hand-roll fusion.
    """
    return await store.search_preferences_hybrid(applicant_profile_id, query_text, top_k=top_k)


@mcp.tool(
    title="get_candidate_proof_points",
    tags={"job_search_plugin", "profile", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_candidate_proof_points(
    applicant_profile_id: int,
    keywords: list[str],
    max_bullets: int = 5,
) -> dict:
    """Searches resume chunks and returns top matching achievement bullets.

    Lets Career-Ops query only the proof points relevant to one JD requirement instead
    of pulling the whole resume. Hybrid (dense+sparse RRF) retrieval via the Stage 4
    search_engine adapter for job_preferences.
    """
    if not keywords:
        return {"status": "error", "error": "invalid_keywords"}

    profile = await store.get_profile(applicant_profile_id)
    if profile is None:
        return {"status": "error", "error": "profile_not_found"}

    query_text = " ".join(keywords)
    chunks = await _retrieve_chunks(applicant_profile_id, query_text, max_bullets)

    return {
        "status": "ok",
        "proof_points": [
            {
                "content": c.get("content"),
                "source": c.get("source"),
                "similarity": c.get("similarity"),
            }
            for c in chunks
        ],
        "hybrid_used": True,
    }
