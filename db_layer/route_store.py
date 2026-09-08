"""
route_store — data-access helpers for route_embeddings admin operations.

Route handlers in api/route_routes.py own HTTP shaping; this module owns the
parameterized SQL against route_embeddings (and related embedding_jobs cleanup).
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from sqlalchemy import delete, text
from db_layer.models import RouteEmbedding, EmbeddingJob

from db_layer.connection import get_async_session

logger = logging.getLogger("whiskers")


async def list_routes_for_plugin(
    plugin_id: str, limit: int = 200, offset: int = 0
) -> Sequence[Any]:
    """Return individual route_embeddings rows for a plugin (ordered by operation_id)."""
    async with get_async_session() as session:
        rows = await session.execute(
            text("""
                SELECT id, plugin_id, operation_id, method,
                       path_template, description,
                       is_enabled, is_fast_path, embedded_at
                FROM route_embeddings
                WHERE plugin_id = :pid
                ORDER BY operation_id
                LIMIT :limit OFFSET :offset
            """),
            {"pid": plugin_id, "limit": limit, "offset": offset},
        )
        return rows.all()


async def list_route_groups() -> Sequence[Any]:
    """Return aggregate route-embedding counts grouped by plugin_id."""
    async with get_async_session() as session:
        rows = await session.execute(
            text("""
                SELECT plugin_id,
                       COUNT(*)                                   AS route_count,
                       COUNT(*) FILTER (WHERE is_enabled = TRUE)  AS enabled_count,
                       BOOL_AND(is_enabled)                       AS all_enabled,
                       MIN(embedded_at)                           AS first_embedded_at
                FROM route_embeddings
                GROUP BY plugin_id
                ORDER BY plugin_id
            """)
        )
        return rows.all()


async def set_plugin_routes_enabled(plugin_id: str, enabled: bool) -> int:
    """Set is_enabled for all route_embeddings of a plugin. Returns rowcount."""
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                UPDATE route_embeddings
                SET is_enabled = :enabled
                WHERE plugin_id = :pid
            """),
            {"pid": plugin_id, "enabled": enabled},
        )
        await session.commit()
        return int(result.rowcount or 0)


async def set_route_enabled(plugin_id: str, route_id: int, enabled: bool) -> int:
    """Set is_enabled for a single route_embeddings row. Returns rowcount."""
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                UPDATE route_embeddings
                SET is_enabled = :enabled
                WHERE id = :rid AND plugin_id = :pid
            """),
            {"rid": route_id, "pid": plugin_id, "enabled": enabled},
        )
        await session.commit()
        return int(result.rowcount or 0)


async def set_route_enabled_by_operation(
    plugin_id: str, operation_id: str, enabled: bool
) -> int:
    """Set is_enabled for route_embeddings matching plugin + operation_id. Returns rowcount."""
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                UPDATE route_embeddings
                SET is_enabled = :enabled
                WHERE plugin_id = :pid AND operation_id = :oid
            """),
            {"pid": plugin_id, "oid": operation_id, "enabled": enabled},
        )
        await session.commit()
        return int(result.rowcount or 0)


async def delete_plugin_route_embeddings(plugin_id: str) -> int:
    """Delete route_embeddings and embedding_jobs for a plugin. Returns embeddings deleted."""
    async with get_async_session() as session:
        result = await session.execute(
            delete(RouteEmbedding).where(RouteEmbedding.plugin_id == plugin_id)
        )
        await session.execute(
            delete(EmbeddingJob).where(EmbeddingJob.plugin_id == plugin_id)
        )
        await session.commit()
        return int(result.rowcount or 0)


async def delete_all_route_embeddings() -> int:
    """Delete all route_embeddings and embedding_jobs. Returns embeddings deleted."""
    async with get_async_session() as session:
        result = await session.execute(delete(RouteEmbedding))
        await session.execute(delete(EmbeddingJob))
        await session.commit()
        return int(result.rowcount or 0)
