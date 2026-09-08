"""
MCP Tools for managing and submitting job applications.
"""

import logging
import requests
from core.context import mcp
from utils import safe_api_call
from plugins.job_search_plugin import store

logger = logging.getLogger("whiskers.plugins")

from plugins.job_search_plugin.plugin_config import SETTINGS

# Boards with a real REST apply endpoint
APPLY_ENDPOINTS = SETTINGS.get("apply_endpoints", {
    "greenhouse": "https://api.greenhouse.io/v1/apply",
})


@mcp.tool(
    title="create_application",
    tags={"job_search_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def create_application(
    job_id: str,
    provider: str,
    applicant_profile_id: int,
    resume_object_key: str = "",
    cover_letter_object_key: str = "",
    notes: str = "",
    portfolio_job_id: str = "",
) -> dict:
    """Create a new job application tracking record.

    Validates required fields and defaults status to 'drafted'. Optionally
    records the "bake & send" portfolio_job_id (see portfolio_plugin bake_tools) for
    traceability back to the baked portfolio layout artifact.
    """
    missing = []
    if not job_id:
        missing.append("job_id")
    if not provider:
        missing.append("provider")
    if applicant_profile_id is None:
        missing.append("applicant_profile_id")

    if missing:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": missing,
            "message": "Please provide: " + "; ".join(missing),
        }

    app = await store.create_application(
        applicant_profile_id,
        job_id,
        provider,
        resume_object_key=resume_object_key,
        cover_letter_object_key=cover_letter_object_key,
        notes=notes,
        portfolio_job_id=portfolio_job_id or None,
    )
    return app


@mcp.tool(
    title="submit_application",
    tags={"job_search_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def submit_application(application_id: int) -> dict:
    """Submit a job application.

    For REST-apply boards, submits via REST API. Otherwise returns a
    needs_browser_apply payload to prompt browser automation.
    """
    application = await store.get_application(application_id)
    if not application:
        return {"status": "error", "error": "application_not_found"}

    profile = await store.get_profile(application["applicant_profile_id"])
    if not profile:
        return {"status": "error", "error": "profile_not_found"}

    provider = application.get("provider", "").lower()
    if provider in APPLY_ENDPOINTS:
        url = APPLY_ENDPOINTS[provider]
        payload = {
            "job_id": application.get("job_id"),
            "full_name": profile.get("full_name"),
            "email": profile.get("email"),
            "phone": profile.get("phone"),
            "resume_object_key": application.get("resume_object_key"),
            "cover_letter_object_key": application.get("cover_letter_object_key"),
            "notes": application.get("notes"),
        }

        def fetch():
            """Perform POST request to submit job application to the provider."""
            return requests.post(url, json=payload, timeout=30)

        api_resp = await safe_api_call(
            fetch,
            lambda resp: resp.json(),
            context=f"Submitting job application to {provider} API",
            raw_response=True,
        )

        if isinstance(api_resp, dict) and api_resp.get("status") == "error":
            return api_resp

        provider_app_id = str(api_resp.get("id") or api_resp.get("provider_application_id") or "")
        updated_app = await store.update_application(
            application_id,
            status="submitted",
            provider_application_id=provider_app_id,
        )

        return {
            "status": "ok",
            "submitted": True,
            "application": updated_app,
        }
    else:
        return {
            "status": "needs_browser_apply",
            "application_id": application_id,
            "profile": profile,
            "resume_object_key": application.get("resume_object_key", ""),
        }


@mcp.tool(
    title="mark_application_submitted",
    tags={"job_search_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def mark_application_submitted(
    application_id: int,
    provider_application_id: str = "",
) -> dict:
    """Manually mark an application as submitted.

    Updates status to 'submitted' and stores the provider application ID.
    """
    updated_app = await store.update_application(
        application_id,
        status="submitted",
        provider_application_id=provider_application_id,
    )
    if not updated_app:
        return {"status": "error", "error": "application_not_found"}
    return updated_app


@mcp.tool(
    title="update_application",
    tags={"job_search_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def update_application(
    application_id: int,
    status: str = "",
    notes: str = "",
    provider_application_id: str = "",
) -> dict:
    """Update job application details.

    Validates status parameter if provided. Delegates to store update.
    """
    fields = {}
    if status:
        valid_statuses = {"drafted", "submitted", "interview", "offer", "rejected", "withdrawn"}
        if status not in valid_statuses:
            return {
                "status": "error",
                "error": "invalid_status",
                "message": f"Status must be one of: {sorted(list(valid_statuses))}",
            }
        fields["status"] = status

    if notes:
        fields["notes"] = notes

    if provider_application_id:
        fields["provider_application_id"] = provider_application_id

    updated_app = await store.update_application(application_id, **fields)
    if not updated_app:
        return {"status": "error", "error": "application_not_found"}
    return updated_app


@mcp.tool(
    title="list_applications",
    tags={"job_search_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def list_applications(
    applicant_profile_id: int | None = None,
    status_filter: str = "",
) -> dict:
    """List job applications, optionally filtering by profile ID or status."""
    status = status_filter if status_filter else None
    apps = await store.list_applications(applicant_profile_id=applicant_profile_id, status=status)
    return {
        "status": "ok",
        "count": len(apps),
        "applications": apps,
    }


_SYNC_STATUSES = {"drafted", "applied", "interviewing", "offer", "rejected"}


@mcp.tool(
    title="sync_application_status",
    tags={"job_search_plugin", "application", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def sync_application_status(
    applicant_profile_id: int,
    job_id: str,
    status: str = "",
    notes: str = "",
    portfolio_job_id: str = "",
    application_status: str = "",
) -> dict:
    """Idempotently creates or updates an application status, keyed on (profile, job_id).

    Built for Career-Ops's own lifecycle vocabulary (drafted/applied/interviewing/offer/
    rejected) — distinct from update_application's provider-submission statuses. No DB
    unique constraint backs the key (store.upsert_application_status uses an advisory
    lock instead); safe to call repeatedly as the CLI's view of a role's status changes.

    application_status is an alias for status, accepted so a FlowSpec blackboard stage
    (career_ops_apply_v1's "track" stage) can feed this tool without a slot name that
    collides with the envelope's own "status" key on prepare_application_record's output.
    status still wins if both are given.
    """
    status = status or application_status
    if status not in _SYNC_STATUSES:
        return {
            "status": "error",
            "error": "invalid_status",
            "message": f"Status must be one of: {sorted(_SYNC_STATUSES)}",
        }
    if not job_id:
        return {"status": "error", "error": "missing_required_fields", "missing_fields": ["job_id"]}

    app, created = await store.upsert_application_status(
        applicant_profile_id,
        job_id,
        status,
        notes=notes,
        portfolio_job_id=portfolio_job_id,
    )
    return {"status": "ok", "application": app, "created": created}


@mcp.tool(
    title="get_agent_status",
    tags={"job_search_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_agent_status(
    job_id: str = "",
    portfolio_job_id: str = "",
) -> dict:
    """Privacy-safe status snapshot of the most recent job application.

    Returns only ``{job_id, status, updated_at}`` — never applicant PII — because
    this backs the public portfolio's live-status widget. Exposed as a catalog
    operation so portfolio_plugin can reach it via ``execute_operation`` instead
    of importing this plugin's store.
    """
    status = await store.get_latest_agent_status(
        job_id=job_id or None,
        portfolio_job_id=portfolio_job_id or None,
    )
    return {"status": "ok", "activity": status}
