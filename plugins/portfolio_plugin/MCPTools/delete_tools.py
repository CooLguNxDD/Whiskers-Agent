"""Operator delete tools for portfolio data (projects, job layouts, bake runs).

Every tool is dry-run-first (``dry_run=True`` by default), takes the tenant from
the authenticated principal only (``require_tenant_id``; no ``tenant_id``
argument) and fails closed when it is unresolved. Dry runs perform no writes and
return the same keys a real run would. Not-found is a clean result, not an
exception. Tagged ``delete``; never GOAP plan steps.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from core.context import mcp

logger = logging.getLogger("whiskers.plugins.portfolio.delete_tools")

# Max run ids echoed back by delete_bake_runs (total is always reported).
BAKE_RUN_ID_LIST_CAP = 200

_PROJECT_RECREATE_WARNING = (
    "discovery reconcile can re-create this project from GitHub; "
    "set is_active=false to hide it instead of deleting"
)


def _tenant_or_error() -> tuple[int | None, dict[str, Any] | None]:
    from plugins.portfolio_plugin.tenant import require_tenant_id

    tid = require_tenant_id()
    if tid is None:
        return None, {"status": "error", "error": "tenant_unresolved"}
    return tid, None


def _missing(field: str) -> dict[str, Any]:
    return {"status": "error", "error": "missing_required_fields", "missing_fields": [field]}


@mcp.tool(
    title="delete_project",
    tags={"portfolio_plugin", "delete"},
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
)
async def delete_project(slug: str, dry_run: bool = True) -> dict[str, Any]:
    """Delete a ``portfolio_projects`` row and its context vectors (tenant-scoped).

    Removes the row for ``slug`` plus ``portfolio_plugin__context`` vectors whose
    ``meta.slug_hint == slug``. ``dry_run`` (default true) writes nothing and
    reports what would be removed. Tenant comes from the principal only.
    """
    from db_layer import search_content_vectors_store as vstore
    from plugins.portfolio_plugin import store
    from plugins.portfolio_plugin.discovery.index import CONTEXT_COLLECTION

    tid, err = _tenant_or_error()
    if err:
        return err
    slug = slug.strip() if isinstance(slug, str) else ""
    if not slug:
        return _missing("slug")
    try:
        row = await store.get_project(slug, tenant_id=tid)
        vectors = await vstore.count_search_content_vectors_by_meta(
            CONTEXT_COLLECTION, "slug_hint", slug, tenant_id=tid
        )
        if row is None and vectors == 0:
            return {"status": "not_found", "dry_run": bool(dry_run), "slug": slug}
        result: dict[str, Any] = {
            "status": "ok",
            "dry_run": bool(dry_run),
            "slug": slug,
            "project": (
                {"slug": row.get("slug"), "name": row.get("name"), "is_active": row.get("is_active")}
                if row
                else None
            ),
            "project_deleted": row is not None,
            "vectors_deleted": vectors,
            "vector_collection": CONTEXT_COLLECTION,
            "warnings": [_PROJECT_RECREATE_WARNING],
        }
        if dry_run:
            return result
        # Vectors first: a failed row delete leaves a retryable state, not orphans.
        if vectors:
            result["vectors_deleted"] = await vstore.delete_search_content_vectors_by_meta(
                CONTEXT_COLLECTION, "slug_hint", slug, tenant_id=tid
            )
        if row is not None:
            result["project_deleted"] = await store.delete_project(slug, tid)
        return result
    except Exception:
        logger.error("delete_project failed", exc_info=True)
        return {"status": "error", "error": "delete_failed", "dry_run": bool(dry_run)}


# ``static_tool_loader._qualify`` registers ``@mcp.tool`` ops as ``<plugin_id>__<name>``.
_JOB_SEARCH_PLUGIN = "job_search_plugin"
_JOB_APPS_OP = "list_applications_for_portfolio_job"
_JOB_APPS_OP_QUALIFIED = f"{_JOB_SEARCH_PLUGIN}__{_JOB_APPS_OP}"


async def _portfolio_job_applications(short_id: str) -> tuple[list[dict] | None, str | None]:
    """Applications referencing *short_id* via catalog dispatch; ``(None, why)`` if unavailable."""
    try:
        from core.route_registry.execute import ExecuteError, execute_operation

        args = {"portfolio_job_id": short_id}
        try:
            out = await execute_operation(
                _JOB_SEARCH_PLUGIN,
                _JOB_APPS_OP_QUALIFIED,
                args,
                caller_scopes=None,  # trusted-local, same as discovery.sources.invoke_proxy
            )
        except ExecuteError as exc:
            if exc.code != "not_found":
                raise
            # Unqualified fallback for a catalog that registers the bare name.
            out = await execute_operation(
                _JOB_SEARCH_PLUGIN, _JOB_APPS_OP, args, caller_scopes=None
            )
    except Exception as exc:
        return None, type(exc).__name__
    if isinstance(out, dict) and "applications" not in out and isinstance(out.get("data"), dict):
        out = out["data"]  # flatten {_meta, data} envelope
    if not isinstance(out, dict) or out.get("status") == "error":
        return None, "op_error"
    apps = out.get("applications")
    if not isinstance(apps, list):
        return None, "bad_result"
    return [a for a in apps if isinstance(a, dict)], None


@mcp.tool(
    title="delete_job_layout",
    tags={"portfolio_plugin", "delete"},
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
)
async def delete_job_layout(
    short_id: str,
    force: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Delete a baked ``portfolio_job_layouts`` row and its layout vectors.

    Removes the tenant's row for ``short_id`` plus ``portfolio_plugin__layouts``
    vectors whose ``meta.short_id`` matches. Derived forks are listed as
    ``forks`` but never deleted; bake runs and rendered assets are kept.
    Refuses when job applications still reference the layout unless ``force``;
    if that reference check is unavailable it also refuses unless ``force``.
    """
    from core.memory.registry import plugin_collection
    from db_layer import search_content_vectors_store as vstore
    from plugins.portfolio_plugin import store

    tid, err = _tenant_or_error()
    if err:
        return err
    short_id = short_id.strip() if isinstance(short_id, str) else ""
    if not short_id:
        return _missing("short_id")
    collection = plugin_collection("portfolio_plugin", "layouts")
    try:
        row = await store.get_job_layout_for_tenant(short_id, tid)
        vectors = await vstore.count_search_content_vectors_by_meta(
            collection, "short_id", short_id, tenant_id=tid
        )
        if row is None and vectors == 0:
            return {"status": "not_found", "dry_run": bool(dry_run), "short_id": short_id}
        forks = await store.list_job_layout_forks(short_id, tid)

        apps, why = await _portfolio_job_applications(short_id)
        result: dict[str, Any] = {
            "status": "ok",
            "dry_run": bool(dry_run),
            "short_id": short_id,
            "layout_deleted": row is not None,
            "vectors_deleted": vectors,
            "vector_collection": collection,
            "forks": forks,
            "applications": [a.get("id") for a in apps] if apps is not None else [],
            "reference_check": "ok" if apps is not None else "unavailable",
            "forced": bool(force),
        }
        if apps is None:
            result["reference_check_reason"] = why
        if apps and not force:
            return {**result, "status": "refused", "reason": "referenced_by_applications"}
        if apps is None and not force:
            return {**result, "status": "refused", "reason": "reference_check_unavailable"}
        if dry_run:
            return result
        if vectors:
            result["vectors_deleted"] = await vstore.delete_search_content_vectors_by_meta(
                collection, "short_id", short_id, tenant_id=tid
            )
        if row is not None:
            result["layout_deleted"] = await store.delete_job_layout(short_id, tid)
        return result
    except Exception:
        logger.error("delete_job_layout failed", exc_info=True)
        return {"status": "error", "error": "delete_failed", "dry_run": bool(dry_run)}


@mcp.tool(
    title="delete_bake_runs",
    tags={"portfolio_plugin", "delete"},
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
)
async def delete_bake_runs(
    run_id: str | None = None,
    older_than_days: int | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Delete tenant-scoped ``portfolio_bake_runs`` rows.

    Exactly one selector is required: ``run_id`` (one run) or ``older_than_days``
    (positive int, filters on ``created_at``). Lists the first
    ``BAKE_RUN_ID_LIST_CAP`` run ids plus the total count.
    """
    from plugins.portfolio_plugin import store

    tid, err = _tenant_or_error()
    if err:
        return err
    has_run = run_id is not None
    has_age = older_than_days is not None
    if has_run == has_age:
        return {
            "status": "error",
            "error": "invalid_arguments",
            "message": "provide exactly one of run_id or older_than_days",
        }
    created_before: datetime | None = None
    if has_run:
        run_id = run_id.strip() if isinstance(run_id, str) else ""
        if not run_id:
            return _missing("run_id")
    else:
        if (
            isinstance(older_than_days, bool)
            or not isinstance(older_than_days, int)
            or older_than_days <= 0
        ):
            return {
                "status": "error",
                "error": "invalid_arguments",
                "message": "older_than_days must be a positive integer",
            }
        created_before = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    try:
        total, ids = await store.select_bake_runs_for_delete(
            tenant_id=tid,
            run_id=run_id if has_run else None,
            created_before=created_before,
            limit=BAKE_RUN_ID_LIST_CAP,
        )
        if total == 0:
            return {"status": "not_found", "dry_run": bool(dry_run), "total": 0, "run_ids": []}
        result: dict[str, Any] = {
            "status": "ok",
            "dry_run": bool(dry_run),
            "total": total,
            "run_ids": ids,
            "truncated": total > len(ids),
            "deleted": total,
        }
        if dry_run:
            return result
        result["deleted"] = await store.delete_bake_runs(
            tenant_id=tid,
            run_id=run_id if has_run else None,
            created_before=created_before,
        )
        return result
    except Exception:
        logger.error("delete_bake_runs failed", exc_info=True)
        return {"status": "error", "error": "delete_failed", "dry_run": bool(dry_run)}
