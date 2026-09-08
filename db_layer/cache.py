"""
Simple key/value API response cache backed by PostgreSQL.

Entries have a TTL; expired rows are ignored on read (and can be
periodically cleaned up with a DELETE WHERE expires_at < now()).
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .connection import get_async_session
from .models import ApiCache

logger = logging.getLogger("whiskers")


async def cache_get(key: str) -> dict | None:
    """Return the cached payload for *key*, or None if missing/expired."""
    async with get_async_session() as session:
        stmt = (
            select(ApiCache.payload)
            .where(ApiCache.cache_key == key)
            .where(ApiCache.expires_at > func.now())
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def cache_set(key: str, payload: dict, ttl_seconds: int = 300) -> None:
    """Store *payload* under *key* with the given TTL."""
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    async with get_async_session() as session:
        stmt = (
            insert(ApiCache)
            .values(cache_key=key, payload=payload, expires_at=expires)
            .on_conflict_do_update(
                index_elements=["cache_key"],
                set_=dict(payload=payload, expires_at=expires),
            )
        )
        await session.execute(stmt)
        await session.commit()
