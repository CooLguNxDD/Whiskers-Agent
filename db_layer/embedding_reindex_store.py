"""SQLAlchemy ``ReindexStore`` for ``memory_content_vectors`` / ``search_content_vectors``.

Every statement is filtered by ``tenant_id`` (a missing tenant raises, it never drops
the WHERE). Stale = identity ``model`` differs from the target and the row has source
text. A stale row whose ``(collection, content_hash)`` already exists under the target
identity is *superseded*: rewriting it would violate the unique key, so it is neither
fetched nor updated and is only counted.
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import and_, bindparam, exists, func, select, update
from sqlalchemy.orm import aliased

from db_layer.connection import get_async_session
from db_layer.embeddings.reindex import StaleRow
from db_layer.models import MemoryContentVector, SearchContentVector

_MODELS = {"memory": MemoryContentVector, "search": SearchContentVector}


def _model(collection: str):
    try:
        return _MODELS[collection]
    except KeyError:
        raise ValueError(f"unknown reindex collection {collection!r}") from None


def _tenant(tenant_id: int) -> int:
    if tenant_id is None:
        raise ValueError("tenant_id is required")
    return tenant_id


def _has_text(model):
    return and_(model.content_text.is_not(None), func.length(func.btrim(model.content_text)) > 0)


def _twin_exists(model, target: str):
    twin = aliased(model)
    return exists().where(
        twin.collection == model.collection,
        twin.content_hash == model.content_hash,
        twin.model == target,
    )


class SqlReindexStore:
    """Postgres/pgvector implementation of ``ReindexStore``."""

    async def list_tenants(self) -> list[int]:
        tenants: set[int] = set()
        async with get_async_session() as session:
            for model in _MODELS.values():
                rows = await session.execute(
                    select(model.tenant_id).where(model.tenant_id.is_not(None)).distinct()
                )
                tenants.update(int(r[0]) for r in rows.all())
        return sorted(tenants)

    async def vector_width(self, collection: str) -> int | None:
        from core.embedding_dimensions import vector_column_width

        table = _model(collection).__tablename__
        async with get_async_session() as session:
            conn = await session.connection()
            return await conn.run_sync(lambda sync_conn: vector_column_width(sync_conn, table))

    async def count_rows(self, collection: str, tenant_id: int, target_identity: str) -> dict[str, int]:
        m = _model(collection)
        tenant_id = _tenant(tenant_id)
        stale_identity = m.model != target_identity
        twin = _twin_exists(m, target_identity)
        stmt = select(
            func.count(),
            func.count().filter(m.model == target_identity),
            func.count().filter(and_(stale_identity, _has_text(m), ~twin)),
            func.count().filter(and_(stale_identity, ~_has_text(m))),
            func.count().filter(and_(stale_identity, _has_text(m), twin)),
        ).where(m.tenant_id == tenant_id)
        async with get_async_session() as session:
            total, done, stale, no_text, superseded = (await session.execute(stmt)).one()
        return {
            "total": total, "already_target": done, "stale": stale,
            "no_text": no_text, "superseded": superseded,
        }

    async def fetch_stale(
        self, collection: str, tenant_id: int, target_identity: str, after_id: int | None, limit: int
    ) -> list[StaleRow]:
        m = _model(collection)
        stmt = (
            select(m.id, m.content_text)
            .where(m.tenant_id == _tenant(tenant_id))
            .where(m.model != target_identity)
            .where(_has_text(m))
            .where(~_twin_exists(m, target_identity))
            .order_by(m.id)
            .limit(limit)
        )
        if after_id is not None:
            stmt = stmt.where(m.id > after_id)
        async with get_async_session() as session:
            rows = (await session.execute(stmt)).all()
        return [StaleRow(id=r[0], text=r[1]) for r in rows]

    async def update_batch(
        self,
        collection: str,
        tenant_id: int,
        target_identity: str,
        items: Sequence[tuple[int, list[float]]],
    ) -> int:
        if not items:
            return 0
        m = _model(collection)
        table = m.__table__
        # One executemany statement, one transaction: the batch lands whole or not at all.
        stmt = (
            update(table)
            .where(table.c.id == bindparam("b_id"))
            .where(table.c.tenant_id == _tenant(tenant_id))
            .where(table.c.model != target_identity)
            .values(embedding=bindparam("b_vec"), model=target_identity)
        )
        async with get_async_session() as session:
            await session.execute(stmt, [{"b_id": i, "b_vec": v} for i, v in items])
            await session.commit()
        return len(items)
