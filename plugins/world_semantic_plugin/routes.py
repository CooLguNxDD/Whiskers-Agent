"""HTTP routes for Unity bulk index / diff ingest (zero LLM tokens).

Index path is **async queue**: POST enqueues a world_index_jobs row and the
``world_index_worker`` (WorkerRegistry) claims/processes it. Clients poll job
status endpoints instead of waiting for a one-shot full_index.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from core.context import http_route_registry
from core.http_route_registry import AuthPolicy
from plugins.world_semantic_plugin.stores import apply_diff, get_world
from plugins.world_semantic_plugin.stores.job_store import (
    enqueue_index_job,
    get_job,
    list_jobs,
    queue_stats,
)

logger = logging.getLogger("whiskers.plugins.world_semantic")

OWNER = "world_semantic_plugin"
INDEX_PATH = "/api/world/{world_id}/index"
DIFF_PATH = "/api/world/{world_id}/diff"
WORLD_PATH = "/api/world/{world_id}"
JOB_PATH = "/api/world/{world_id}/index/jobs/{job_id}"
JOBS_PATH = "/api/world/{world_id}/index/jobs"


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def world_index(request: Request) -> JSONResponse:
    """POST scene index batch → enqueue world_index_jobs (202 Accepted).

    Body:
      objects | items: list[object]
      replace: bool (default true) — wipe world objects before this batch
      world | projection: meta for ensure_world
      batch_index / batch_total: optional client batch numbering
      client_run_id: optional id tying multi-batch runs together

    Query:
      sync=1 — legacy one-shot full_index (blocking; not recommended)
    """
    world_id = request.path_params.get("world_id")
    if not world_id:
        return JSONResponse({"status": "error", "error": "missing_world_id"}, status_code=400)

    body = await _read_json(request)
    objects = body.get("objects") or body.get("items") or []
    if not isinstance(objects, list):
        return JSONResponse(
            {"status": "error", "error": "objects_must_be_list"},
            status_code=400,
        )

    replace = body.get("replace", True)
    world_meta = body.get("world") or body.get("projection") or {}
    batch_index = body.get("batch_index")
    batch_total = body.get("batch_total")
    client_run_id = body.get("client_run_id") or body.get("run_id")
    # embed=false → spatial index only (skip unity_world_vectors fan-out)
    embed_flag = body.get("embed", True)
    if isinstance(embed_flag, str):
        embed_flag = embed_flag.lower() not in ("0", "false", "no")
    embed_flag = bool(embed_flag)

    # Optional escape hatch for tiny tests / debugging.
    sync = str(request.query_params.get("sync", "")).lower() in ("1", "true", "yes")
    if sync:
        try:
            from plugins.world_semantic_plugin.stores import full_index

            result = await full_index(
                world_id,
                objects,
                replace=bool(replace),
                world_meta=world_meta if isinstance(world_meta, dict) else {},
            )
            if embed_flag:
                try:
                    from plugins.world_semantic_plugin.stores.embed_job_store import (
                        enqueue_unity_world_embed_jobs,
                    )

                    emb = await enqueue_unity_world_embed_jobs(
                        world_id,
                        objects,
                        touched_hex_ids=result.get("touched_hex_ids") or [],
                        replace=bool(replace),
                    )
                    result["unity_embed_jobs_enqueued"] = emb.get("enqueued", 0)
                    result["unity_embed"] = emb
                except Exception:
                    logger.exception("world_index sync embed enqueue failed")
                    result["unity_embed_jobs_enqueued"] = 0
            return JSONResponse(result)
        except Exception:
            logger.exception("world_index sync failed for %s", world_id)
            return JSONResponse(
                {"status": "error", "error": "index_failed"},
                status_code=500,
            )

    try:
        # Ensure world row exists up front so GET /world succeeds while jobs run.
        from plugins.world_semantic_plugin.stores import ensure_world

        meta = dict(world_meta) if isinstance(world_meta, dict) else {}
        # Stash embed flag for world_index_worker (not a projection column).
        meta["embed"] = embed_flag
        await ensure_world(
            world_id,
            name=meta.get("name"),
            anchor_lat=meta.get("anchor_lat"),
            anchor_lng=meta.get("anchor_lng"),
            meters_per_degree=meta.get("meters_per_degree"),
            base_res=meta.get("base_res"),
            layer_height=meta.get("layer_height"),
        )

        queued = await enqueue_index_job(
            world_id,
            objects,
            replace=bool(replace),
            world_meta=meta,
            batch_index=int(batch_index) if batch_index is not None else None,
            batch_total=int(batch_total) if batch_total is not None else None,
            client_run_id=str(client_run_id) if client_run_id else None,
        )
        # Nudge worker if registry is available (LISTEN optional; poll loop also works).
        try:
            from core_graph.worker.worker_registry import get_worker_registry

            reg = get_worker_registry()
            if not reg.is_running("world_index_worker"):
                from plugins.world_semantic_plugin.worker import register as reg_worker

                # Idempotent re-register + start if plugin loaded before registry existed.
                reg_worker(reg)
                reg.start("world_index_worker")
        except Exception:
            logger.debug("world_index: worker nudge skipped", exc_info=True)

        return JSONResponse(queued, status_code=202)
    except Exception:
        logger.exception("world_index enqueue failed for %s", world_id)
        return JSONResponse(
            {"status": "error", "error": "enqueue_failed"},
            status_code=500,
        )


async def world_index_job_get(request: Request) -> JSONResponse:
    """GET status of one index job."""
    world_id = request.path_params.get("world_id")
    job_id_raw = request.path_params.get("job_id")
    try:
        job_id = int(job_id_raw)
    except (TypeError, ValueError):
        return JSONResponse({"status": "error", "error": "invalid_job_id"}, status_code=400)

    job = await get_job(job_id)
    if not job:
        return JSONResponse({"status": "error", "error": "job_not_found"}, status_code=404)
    if world_id and job.get("world_id") != world_id:
        return JSONResponse({"status": "error", "error": "job_world_mismatch"}, status_code=404)
    return JSONResponse({"status": "ok", "job": job})


async def world_index_jobs_list(request: Request) -> JSONResponse:
    """GET recent index jobs for a world (+ optional filters)."""
    world_id = request.path_params.get("world_id")
    if not world_id:
        return JSONResponse({"status": "error", "error": "missing_world_id"}, status_code=400)

    status = request.query_params.get("status")
    client_run_id = request.query_params.get("client_run_id") or request.query_params.get("run_id")
    try:
        limit = int(request.query_params.get("limit") or 50)
    except ValueError:
        limit = 50

    jobs = await list_jobs(
        world_id,
        status=status,
        client_run_id=client_run_id,
        limit=limit,
    )
    stats = await queue_stats(world_id)
    return JSONResponse({"status": "ok", "jobs": jobs, "queue": stats})


async def world_diff(request: Request) -> JSONResponse:
    """POST batched ObjectChangeEvents diffs from Unity ChangeWatcher."""
    world_id = request.path_params.get("world_id")
    if not world_id:
        return JSONResponse({"status": "error", "error": "missing_world_id"}, status_code=400)

    body = await _read_json(request)
    ops = body.get("ops") or body.get("diff") or body.get("changes") or []
    if not isinstance(ops, list):
        return JSONResponse({"status": "error", "error": "ops_must_be_list"}, status_code=400)

    if not await get_world(world_id):
        from plugins.world_semantic_plugin.stores import ensure_world

        await ensure_world(world_id)

    try:
        result = await apply_diff(world_id, ops)
        return JSONResponse(result)
    except Exception:
        logger.exception("world_diff failed for %s", world_id)
        return JSONResponse(
            {"status": "error", "error": "diff_failed"},
            status_code=500,
        )


async def world_get(request: Request) -> JSONResponse:
    """GET world metadata (projection params)."""
    world_id = request.path_params.get("world_id")
    row = await get_world(world_id) if world_id else None
    if not row:
        return JSONResponse({"status": "error", "error": "world_not_found"}, status_code=404)
    out = dict(row)
    for key in ("created_at", "updated_at"):
        if out.get(key) is not None:
            out[key] = out[key].isoformat() if hasattr(out[key], "isoformat") else str(out[key])
    stats = await queue_stats(world_id)
    return JSONResponse({"status": "ok", "world": out, "index_queue": stats})


def register_routes() -> None:
    """Register HTTP routes for world_semantic_plugin."""
    # PUBLIC for local Unity editor bulk path (step 1). Gate with API keys later.
    # More specific job routes first.
    http_route_registry.register_http_route(
        JOB_PATH,
        world_index_job_get,
        methods=["GET"],
        name="world_index_job_get",
        owner=OWNER,
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        JOBS_PATH,
        world_index_jobs_list,
        methods=["GET"],
        name="world_index_jobs_list",
        owner=OWNER,
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        INDEX_PATH,
        world_index,
        methods=["POST"],
        name="world_index",
        owner=OWNER,
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        DIFF_PATH,
        world_diff,
        methods=["POST"],
        name="world_diff",
        owner=OWNER,
        auth_policy=AuthPolicy.PUBLIC,
    )
    http_route_registry.register_http_route(
        WORLD_PATH,
        world_get,
        methods=["GET"],
        name="world_get",
        owner=OWNER,
        auth_policy=AuthPolicy.PUBLIC,
    )
