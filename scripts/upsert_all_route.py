#!/usr/bin/env python3
"""
High-performance script to discover, validate, and queue embeddings for all plugin routes.
Instead of processing routes one by one, it boots the plugin loader and registry,
discovers all active routes, and enqueues them in the database for the background
embedding worker to index.
"""

import argparse
import asyncio
import hashlib
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("whiskers.upsert_all_route_report")

_DB_AVAILABLE = bool(os.environ.get("DATABASE_URL", "").strip())
if not _DB_AVAILABLE:
    logger.error("DATABASE_URL not set. Exiting.")
    sys.exit(1)








async def _prune_stale(active_keys: set[tuple[str, str]]) -> int:
    """Delete embedding rows whose (plugin_id, operation_id) is no longer registered.

    Also resets the matching ``embedding_jobs`` row back to ``pending`` (unless
    mid-flight) so a pruned route can re-embed instead of staying stuck behind
    an already-``done`` job that ``enqueue_pending`` never resurrects.
    """
    from db_layer.connection import get_async_session
    from sqlalchemy import text
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


async def main():
    """Main execution function for route upsert enqueuing.

    1-line comment: Boots the plugins, collects all routes, compares them to DB state, and enqueues them.
    """
    parser = argparse.ArgumentParser(
        description="High-performance script to discover, validate, and queue embeddings for all plugin routes."
    )
    parser.add_argument(
        "-n",
        "--number",
        type=int,
        default=None,
        help="Limit the number of routes to process (default: all)"
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="Force re-embedding of already indexed routes"
    )
    args = parser.parse_args()

    # 1. Boot the registry and load plugins
    from core.plugin_loader.plugin_registry import PluginRegistry, _set_registry
    from core.plugin_loader.plugin_loader import discover_and_load_plugins_async
    from core.context import mcp, vault, oauth_relay, route_registry
    from db_layer.embeddings.embeddings_routes import existing_route_hashes, disabled_route_keys
    
    logger.info("Starting Whiskers Agent Plugin loader and registry...")
    registry = PluginRegistry(mcp, vault=vault, relay=oauth_relay)
    _set_registry(registry)
    
    await discover_and_load_plugins_async(registry)
    await registry.lifecycle.initialize_plugins()
    logger.info("All plugins initialized successfully.")

    # 2. Get active routes
    routes = route_registry.all_routes()
    active_keys = {r.key for r in routes}
    logger.info("Discovered %d active route(s) from registry.", len(routes))

    if not routes:
        logger.info("No routes found to process.")
        return

    # 3. Self-healing stale route pruning step
    try:
        stale = await _prune_stale(active_keys)
        if stale:
            logger.info("Pruned %d stale embedding row(s) no longer in the registry.", stale)
    except Exception as exc:
        logger.warning("Stale-prune step failed: %s", exc)

    # 4. Check existing hashes and disabled keys
    try:
        from core.llm_config_service import resolve_route_embedding
        from db_layer.embeddings.embeddings_core import model_id_for
        sel = await resolve_route_embedding()
        route_model_id = model_id_for(sel)
        logger.info("Using route embedding model: %s", route_model_id)

        existing = {} if args.override else await existing_route_hashes(route_model_id)
        disabled = await disabled_route_keys()
    except Exception as exc:
        logger.error("Failed to query database state: %s", exc)
        sys.exit(1)

    # 5. Prepare payload rows
    payload_rows = []
    skipped_count = 0
    disabled_count = 0
    already_indexed_count = 0

    for r in routes:
        if r.key in disabled:
            logger.info("Skipping disabled route: %s (plugin_id=%s, operation_id=%s)", r.key, r.plugin_id, r.operation_id)
            disabled_count += 1
            skipped_count += 1
            continue

        eff_hash = hashlib.sha256(f"{r.content_hash}|{route_model_id}".encode()).hexdigest()
        if not args.override and existing.get(r.key) == eff_hash:
            already_indexed_count += 1
            skipped_count += 1
            continue

        if args.number is not None and len(payload_rows) >= args.number:
            break

        payload_rows.append({
            "plugin_id": r.plugin_id,
            "operation_id": r.operation_id,
            "content_hash": eff_hash,
            "payload": r.to_payload(),
        })

    # 6. Enqueue batch into EmbeddingJob
    if payload_rows:
        logger.info("Enqueuing %d route embedding jobs in a single batch...", len(payload_rows))
        try:
            from db_layer.connection import get_async_session
            from db_layer.models import EmbeddingJob
            from sqlalchemy.dialects.postgresql import insert
            from sqlalchemy import text

            async with get_async_session() as session:
                stmt = insert(EmbeddingJob).values(payload_rows)
                # DO UPDATE so identical content resurrects failed jobs.
                # override=True resets even done jobs to pending (except processing).
                status_filter = (
                    (EmbeddingJob.status != "processing") if args.override
                    else (EmbeddingJob.status == "failed")
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["plugin_id", "operation_id", "content_hash"],
                    set_={
                        "status": "pending",
                        "payload": stmt.excluded.payload,
                        "last_error": None,
                        "claimed_at": None,
                        "completed_at": None,
                    },
                    where=status_filter,
                )
                await session.execute(stmt)
                await session.execute(text("NOTIFY embedding_jobs_new"))
                await session.commit()

            logger.info("=" * 70)
            logger.info("High-Performance Route Enrichment Complete")
            logger.info("=" * 70)
            logger.info("Total Routes Discovered: %d", len(routes))
            logger.info("Total Route Embedding Jobs Queued: %d", len(payload_rows))
            logger.info("Total Skipped (Disabled): %d", disabled_count)
            logger.info("Total Skipped (Already Indexed): %d", already_indexed_count)
            logger.info("=" * 70)

        except Exception as exc:
            logger.error("Enqueue batch failed: %s", exc)
            sys.exit(1)
    else:
        logger.info("=" * 70)
        logger.info("No new route embedding jobs need enqueuing.")
        logger.info("Total Routes Discovered: %d", len(routes))
        logger.info("Total Skipped (Disabled): %d", disabled_count)
        logger.info("Total Skipped (Already Indexed): %d", already_indexed_count)
        logger.info("=" * 70)


if __name__ == "__main__":
    import selectors

    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(main())
