"""MCP delete tools for the Job Search plugin (tenant-scoped, dry-run first).

Every delete tool resolves the tenant from the principal (never an argument),
defaults to ``dry_run=True`` and, on a dry run, returns exactly the keys a real
run returns (``deleted_ids`` etc. then list what *would* be removed). Stored PDFs
are removed only by exact key recorded on a row being deleted, from
``DEFAULT_BUCKET``, through the plugin-facing artifact store.
"""

import logging
from typing import Any

from core.artifact_store import DEFAULT_BUCKET, get_artifact_store
from core.context import mcp
from plugins.job_search_plugin import store
from plugins.job_search_plugin.tenant import require_tenant_id

logger = logging.getLogger("whiskers.plugins")

_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchObject", "NotFound", "404"}


def _unauthorized(message: str = "Missing or invalid tenant context") -> dict[str, Any]:
    """Fail-closed result for an unresolved principal tenant."""
    return {"status": "error", "error": "unauthorized", "message": message}


def _invalid_id(field: str) -> dict[str, Any]:
    """Result for a missing / non-integer / non-positive id argument."""
    return {"status": "error", "error": "missing_required_fields", "missing_fields": [field]}


def _coerce_id(value: Any) -> int | None:
    """Return a positive int id, or None when ``value`` is not one."""
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _is_object_not_found(exc: Exception) -> bool:
    """True when a storage exception means the object is already gone."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _NOT_FOUND_CODES:
        return True
    if isinstance(exc, FileNotFoundError):
        return True
    return type(exc).__name__ in {"NoSuchKey", "NoSuchObject", "ObjectNotFound"}


def _key_is_eligible(key: str, owner_profile_id: int) -> bool:
    """A key may be deleted only under its owning profile's ``{id}/`` prefix."""
    if not isinstance(key, str) or not key or key.startswith("/") or "\\" in key or "\x00" in key:
        return False
    if not key.startswith(f"{owner_profile_id}/"):
        return False
    segments = key.split("/")
    if any(seg == ".." for seg in segments):
        return False
    return bool(key[len(f"{owner_profile_id}/"):])


def _application_summary(app: dict) -> dict[str, Any]:
    """Row summary reported for a deleted/would-be-deleted application."""
    return {
        "id": app["id"],
        "job_id": app.get("job_id"),
        "provider": app.get("provider"),
        "status": app.get("status"),
    }


async def _cleanup_pdfs(
    keys: list[str | None],
    owner_profile_id: int,
    tenant_id: int,
    dry_run: bool,
    exclude_application_ids: list[int] | None = None,
    exclude_profile_id: int | None = None,
) -> tuple[dict[str, list[str]], str | None]:
    """Classify and (unless ``dry_run``) remove the PDFs recorded on doomed rows.

    Returns (``{"removed", "kept_shared", "skipped_invalid"}``, failing_key). On a
    dry run ``removed`` lists what a real run would remove and nothing is called
    on the artifact store. A hard ``remove_bytes`` failure stops the loop and
    returns the failing key; a missing object counts as removed.
    """
    result: dict[str, list[str]] = {"removed": [], "kept_shared": [], "skipped_invalid": []}
    eligible: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key is None or key in seen:
            continue
        seen.add(key)
        if _key_is_eligible(key, owner_profile_id):
            eligible.append(key)
        else:
            result["skipped_invalid"].append(key)

    shared = await store.find_shared_object_keys(
        eligible,
        tenant_id,
        exclude_application_ids=exclude_application_ids,
        exclude_profile_id=exclude_profile_id,
    )
    to_remove = [k for k in eligible if k not in shared]
    result["kept_shared"] = [k for k in eligible if k in shared]

    if dry_run:
        result["removed"] = to_remove
        return result, None

    artifact_store = get_artifact_store()
    for key in to_remove:
        try:
            await artifact_store.remove_bytes(DEFAULT_BUCKET, key)
        except Exception as exc:
            if not _is_object_not_found(exc):
                logger.error("job_search delete: remove_bytes failed for %s", key, exc_info=True)
                return result, key
        result["removed"].append(key)
    return result, None


def _pdf_failure(
    key: str, pdf_keys: dict[str, list[str]], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Error result for a hard storage failure; the DB row was kept."""
    return {
        "status": "error",
        "error": "pdf_cleanup_failed",
        "message": "Object storage delete failed; database row kept.",
        "key": key,
        "dry_run": False,
        "pdf_keys": pdf_keys,
        **(extra or {}),
    }


@mcp.tool(
    title="delete_application",
    tags={"job_search_plugin", "delete"},
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
)
async def delete_application(id: int, dry_run: bool = True) -> dict:
    """Delete one job application (tenant-scoped) and its stored resume/cover PDFs.

    ``dry_run`` (default True) writes nothing and reports what a real run would
    remove. PDFs are removed only when the recorded key lives under the owning
    profile's ``{profile_id}/`` prefix and no surviving row still references it.

    Args:
        id: job_applications row id.
        dry_run: True reports only; False deletes.
    """
    tenant_id = require_tenant_id()
    if tenant_id is None:
        return _unauthorized()
    app_id = _coerce_id(id)
    if app_id is None:
        return _invalid_id("id")

    app = await store.get_application_scoped(app_id, tenant_id)
    if app is None:
        return {"status": "not_found", "deleted_ids": [], "dry_run": dry_run}

    pdf_keys, failed_key = await _cleanup_pdfs(
        [app.get("resume_object_key"), app.get("cover_letter_object_key")],
        app["applicant_profile_id"],
        tenant_id,
        dry_run,
        exclude_application_ids=[app_id],
    )
    if failed_key is not None:
        return _pdf_failure(failed_key, pdf_keys)

    if not dry_run:
        if not await store.delete_application_scoped(app_id, tenant_id):
            return {"status": "not_found", "deleted_ids": [], "dry_run": False}
    return {
        "status": "ok",
        "deleted_ids": [app_id],
        "application": _application_summary(app),
        "pdf_keys": pdf_keys,
        "dry_run": dry_run,
    }


@mcp.tool(
    title="delete_applicant_profile",
    tags={"job_search_plugin", "delete"},
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
)
async def delete_applicant_profile(
    id: int, force: bool = False, dry_run: bool = True
) -> dict:
    """Delete an applicant profile (tenant-scoped), cascading applications + embeddings.

    Refuses (``status: "refused"``) while the profile has applications unless
    ``force`` is set. ``dry_run`` (default True) reports counts and PDF keys only.
    PDFs of the profile and of every cascaded application are removed first; a
    hard storage failure keeps the DB row.

    Args:
        id: job_applicant_profiles row id.
        force: delete even when applications exist (they cascade).
        dry_run: True reports only; False deletes.
    """
    tenant_id = require_tenant_id()
    if tenant_id is None:
        return _unauthorized()
    profile_id = _coerce_id(id)
    if profile_id is None:
        return _invalid_id("id")

    profile = await store.get_profile_scoped(profile_id, tenant_id=tenant_id)
    if profile is None:
        return {"status": "not_found", "deleted_ids": [], "dry_run": dry_run}

    apps = await store.list_applications_for_profile_scoped(profile_id, tenant_id)
    app_ids = [a["id"] for a in apps]
    if apps and not force:
        return {
            "status": "refused",
            "error": "has_applications",
            "application_ids": app_ids,
            "applications_count": len(apps),
            "dry_run": dry_run,
        }

    embeddings_deleted = (
        await store.count_profile_embeddings_scoped(profile_id, tenant_id) if dry_run else 0
    )
    keys: list[str | None] = [profile.get("resume_object_key")]
    for a in apps:
        keys.extend([a.get("resume_object_key"), a.get("cover_letter_object_key")])
    pdf_keys, failed_key = await _cleanup_pdfs(
        keys,
        profile_id,
        tenant_id,
        dry_run,
        exclude_application_ids=app_ids,
        exclude_profile_id=profile_id,
    )
    if failed_key is not None:
        return _pdf_failure(failed_key, pdf_keys)

    applications_deleted = len(apps)
    if not dry_run:
        outcome = await store.delete_profile_scoped(profile_id, tenant_id)
        if not outcome["deleted"]:
            return {"status": "not_found", "deleted_ids": [], "dry_run": False}
        applications_deleted = outcome["applications_deleted"]
        embeddings_deleted = outcome["embeddings_deleted"]
    return {
        "status": "ok",
        "deleted_ids": [profile_id],
        "applications_deleted": applications_deleted,
        "application_ids": app_ids,
        "embeddings_deleted": embeddings_deleted,
        "pdf_keys": pdf_keys,
        "dry_run": dry_run,
    }


@mcp.tool(
    title="delete_job_liveness",
    tags={"job_search_plugin", "delete"},
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
)
async def delete_job_liveness(
    url: str | None = None, url_hash: str | None = None, dry_run: bool = True
) -> dict:
    """Delete one job_posting_liveness row (tenant-scoped) by posting URL or url_hash.

    Pass exactly one of ``url`` / ``url_hash``. A row of another tenant (or with
    no tenant) is reported as ``not_found``. ``dry_run`` (default True) reports only.

    Args:
        url: posting URL (hashed with the store's sha256 dedupe key).
        url_hash: sha256 hex digest of the posting URL.
        dry_run: True reports only; False deletes.
    """
    tenant_id = require_tenant_id()
    if tenant_id is None:
        return _unauthorized()
    url = url.strip() or None if isinstance(url, str) else None
    url_hash = url_hash.strip().lower() or None if isinstance(url_hash, str) else None
    if (url is None) == (url_hash is None):
        return {
            "status": "error",
            "error": "invalid_arguments",
            "message": "Provide exactly one of url or url_hash.",
        }
    resolved_hash = url_hash if url_hash is not None else store._hash_url(url)

    row = await store.get_liveness_by_hash(resolved_hash, tenant_id)
    if row is None:
        return {"status": "not_found", "deleted_ids": [], "dry_run": dry_run}
    if not dry_run:
        if not await store.delete_liveness_scoped(resolved_hash, tenant_id):
            return {"status": "not_found", "deleted_ids": [], "dry_run": False}
    return {
        "status": "ok",
        "deleted_ids": [row["id"]],
        "url_hash": resolved_hash,
        "liveness": {
            "id": row["id"],
            "url": row.get("url"),
            "company": row.get("company"),
            "role_title": row.get("role_title"),
        },
        "dry_run": dry_run,
    }


@mcp.tool(
    title="list_applications_for_portfolio_job",
    tags={"job_search_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def list_applications_for_portfolio_job(portfolio_job_id: str) -> dict:
    """List the principal tenant's applications linked to a baked portfolio job.

    Exact match on ``portfolio_job_id``; used before deleting a baked layout to
    see which applications still reference it.

    Args:
        portfolio_job_id: the baked layout short_id.
    """
    tenant_id = require_tenant_id()
    if tenant_id is None:
        return _unauthorized()
    pjid = (portfolio_job_id or "").strip() if isinstance(portfolio_job_id, str) else ""
    if not pjid:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["portfolio_job_id"],
        }
    rows = await store.list_applications_for_portfolio_job_scoped(pjid, tenant_id)
    return {
        "applications": [
            {
                "id": r["id"],
                "applicant_profile_id": r["applicant_profile_id"],
                "job_id": r["job_id"],
                "status": r["status"],
            }
            for r in rows
        ]
    }
