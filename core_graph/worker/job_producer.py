"""
job_producer — enqueues embedding work for routes whose content_hash has
diverged from what's stored in ``route_embeddings``.

Called from ``whiskers_agent_mcp.py`` after ``initialize_plugins()`` so every
plugin has had a chance to contribute. Restart-safe: the
``UNIQUE (plugin_id, operation_id, content_hash)`` constraint on
``embedding_jobs`` plus ``ON CONFLICT DO UPDATE`` means re-running this on
boot is a no-op for routes that are already queued/done, while a previously
``failed`` row for the same content is resurrected back to ``pending`` so the
worker retries it.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from db_layer.connection import get_async_session
from db_layer.models import EmbeddingJob, RouteEmbedding
from db_layer.embeddings.embeddings_routes import existing_route_hashes, disabled_route_keys

from core.route_registry import RouteRegistry

logger = logging.getLogger("whiskers")


def compute_effective_hash(content_hash: str, route_model_id: str) -> str:
    """Compute the schema-version-inclusive effective embedding hash."""
    import hashlib
    from db_layer.embeddings.route_document import DOC_SCHEMA_VERSION
    return hashlib.sha256(f"{content_hash}|{route_model_id}|{DOC_SCHEMA_VERSION}".encode()).hexdigest()


async def _prune_stale(active_keys: set[tuple[str, str]]) -> int:
    """Delete embedding rows whose (plugin_id, operation_id) is no longer registered.

    Self-healing on prefix/rename migrations: when a loader rewrites an
    ``operation_id`` (e.g. adding the ``{plugin_id}__`` prefix), the old row
    in ``route_embeddings`` becomes orphan — present in search results but
    unresolvable in the registry. Pruning at boot keeps the table aligned
    with the live registry.

    Also resets any matching ``embedding_jobs`` row back to ``pending``
    (unless mid-flight): a job that already ran to ``done`` is immune to
    ``enqueue_pending``'s ``WHERE status = 'failed'`` resurrection clause, so
    without this the pruned route's embedding could never regenerate — a
    plugin that's merely inactive for one boot (not actually removed) would
    silently and permanently lose its candidate-search entry.
    """
    async with get_async_session() as session:
        rows = (await session.execute(
            text("SELECT plugin_id, operation_id FROM route_embeddings")
        )).all()
        stale = [
            (r.plugin_id, r.operation_id) for r in rows
            if (r.plugin_id, r.operation_id) not in active_keys
        ]
        if not stale:
            return 0
        pids = [k[0] for k in stale]
        ops = [k[1] for k in stale]
        await session.execute(
            text(
                "DELETE FROM route_embeddings "
                "WHERE (plugin_id, operation_id) IN "
                "(SELECT * FROM unnest(:pids ::text[], :ops ::text[]))"
            ),
            {"pids": pids, "ops": ops},
        )
        await session.execute(
            text(
                "UPDATE embedding_jobs "
                "SET status = 'pending', last_error = NULL, "
                "    claimed_at = NULL, completed_at = NULL "
                "WHERE status != 'processing' "
                "  AND (plugin_id, operation_id) IN "
                "      (SELECT * FROM unnest(:pids ::text[], :ops ::text[]))"
            ),
            {"pids": pids, "ops": ops},
        )
        await session.commit()
    return len(stale)


async def enqueue_pending(registry: RouteRegistry) -> int:
    """Insert pending jobs for routes that need embedding.

    Returns the number of rows newly enqueued (UNIQUE conflicts return 0).
    """
    routes = registry.all_routes()
    active_keys = {r.key for r in routes}

    # Prune stale rows (e.g. left over from a pre-prefix deployment) before
    # diffing — otherwise search_routes() would return un-routable orphans.
    if active_keys:
        try:
            stale = await _prune_stale(active_keys)
            if stale:
                logger.info(
                    "job_producer: pruned %d stale embedding row(s) "
                    "no longer in the registry", stale,
                )
        except Exception as exc:
            logger.warning("job_producer: stale-prune step failed: %s", exc)

    disabled = await disabled_route_keys()
    if disabled:
        routes = [r for r in routes if r.key not in disabled]
        logger.info("job_producer: skipping %d disabled route(s)", len(disabled))
    if not routes:
        logger.info("job_producer: RouteRegistry is empty — nothing to enqueue")
        return 0

    from core.llm_config_service import resolve_route_embedding
    from db_layer.embeddings.embeddings_core import model_id_for

    sel = await resolve_route_embedding()
    route_model_id = model_id_for(sel)

    existing = await existing_route_hashes(route_model_id)
    payload_rows = []
    for r in routes:
        eff_hash = compute_effective_hash(r.content_hash, route_model_id)
        if existing.get(r.key) == eff_hash:
            continue  # already embedded with the current content/model combination
        payload_rows.append({
            "plugin_id": r.plugin_id,
            "operation_id": r.operation_id,
            "content_hash": eff_hash,
            "payload": r.to_payload(),
        })

    if not payload_rows:
        logger.info(
            "job_producer: %d routes registered; embeddings already up-to-date for model %s",
            len(routes), route_model_id,
        )
        return 0

    async with get_async_session() as session:
        stmt = insert(EmbeddingJob).values(payload_rows)
        # DO UPDATE (not DO NOTHING) so a previously failed job for unchanged
        # content is reset to pending and retried; queued/done rows are untouched.
        stmt = stmt.on_conflict_do_update(
            index_elements=["plugin_id", "operation_id", "content_hash"],
            set_={
                "status": "pending",
                "payload": stmt.excluded.payload,
                "last_error": None,
                "claimed_at": None,
                "completed_at": None,
            },
            where=(EmbeddingJob.status == "failed"),
        )
        await session.execute(stmt)
        # NOTIFY is a wake-up signal for the worker — fire whether or not the
        # INSERT actually added rows so a worker waiting on LISTEN gets nudged.
        await session.execute(text("NOTIFY embedding_jobs_new"))
        await session.commit()

    logger.info(
        "job_producer: enqueued %d embedding job(s) (out of %d registered routes)",
        len(payload_rows), len(routes),
    )
    return len(payload_rows)
