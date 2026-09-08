import logging
import os
import time
from typing import Any
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from core.llm_provider_management import get_llm_provider_registry
from db_layer.connection import get_async_session
from db_layer.models import LLMPoolEntry, ServerSetting
from core.llm.vault_registry import encrypt_api_key, invalidate_entry_cache, _get_entry_with_token

logger = logging.getLogger("whiskers")


def _invalidate_role_pool_snapshot() -> None:
    """Best-effort drop of the model-role layer's pool snapshot cache on any pool mutation."""
    try:
        from core_graph.model_roles.resolver import invalidate_pool_snapshot

        invalidate_pool_snapshot()
    except Exception:
        logger.debug("model_roles pool snapshot invalidation skipped", exc_info=True)

_ACTIVE_KEY = "llm_active"
_VALID_KINDS = ("chat", "embedding", "route", "core")
def _db_available() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())

async def list_pool(kind: str | None = None) -> list[dict]:
    """Return pool entries (without the decrypted token; only a ``has_api_key`` flag)."""
    if not _db_available():
        return []
    async with get_async_session() as session:
        stmt = select(
            LLMPoolEntry.id,
            LLMPoolEntry.name,
            LLMPoolEntry.kind,
            LLMPoolEntry.provider,
            LLMPoolEntry.model,
            LLMPoolEntry.dimensions,
            LLMPoolEntry.base_url,
            LLMPoolEntry.strength,
            LLMPoolEntry.is_active,
            (LLMPoolEntry.api_key.is_not(None)).label("has_api_key"),
        ).order_by(LLMPoolEntry.created_at)
        if kind:
            stmt = stmt.where(LLMPoolEntry.kind == kind)
        rows = (await session.execute(stmt)).all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "kind": r.kind,
            "provider": r.provider,
            "model": r.model,
            "dimensions": r.dimensions,
            "base_url": r.base_url,
            "strength": r.strength,
            "is_active": r.is_active,
            "has_api_key": bool(r.has_api_key),
        }
        for r in rows
    ]

async def add_pool_entry(
    name: str,
    provider: str,
    model: str,
    kind: str = "chat",
    dimensions: int | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    strength: float = 1.0,
) -> dict:
    """Insert (or upsert by name) a pool entry. ``api_key`` is encrypted at rest."""
    provider = (provider or "").strip().lower()
    if provider not in get_llm_provider_registry().ids():
        raise ValueError(f"Unsupported provider '{provider}'")
    if kind not in _VALID_KINDS:
        raise ValueError(f"Unsupported kind '{kind}'")

    values: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "provider": provider,
        "model": model,
        "dimensions": dimensions,
        "base_url": base_url or None,
        "strength": strength,
    }
    update_set = dict(values)
    if api_key:
        enc = encrypt_api_key(api_key)
        values["api_key"] = enc
        update_set["api_key"] = enc

    stmt = (
        pg_insert(LLMPoolEntry)
        .values(**values)
        .on_conflict_do_update(index_elements=["name"], set_=update_set)
        .returning(LLMPoolEntry.id)
    )
    async with get_async_session() as session:
        new_id = (await session.execute(stmt)).scalar_one()
        await session.commit()
    # The upsert may have updated an existing entry in place — drop any stale cache.
    invalidate_entry_cache(str(new_id))
    _invalidate_role_pool_snapshot()
    return {"id": str(new_id), "name": name, "kind": kind, "provider": provider, "model": model, "strength": strength}

async def delete_pool_entry(entry_id: str) -> None:
    """Delete a pool entry by id."""
    async with get_async_session() as session:
        await session.execute(delete(LLMPoolEntry).where(LLMPoolEntry.id == entry_id))
        await session.commit()
    invalidate_entry_cache(str(entry_id))
    _invalidate_role_pool_snapshot()

_ACTIVE_MAP_CACHE = {"value": {}, "expires_at": 0.0}
async def get_active_map() -> dict:
    """Return the active-selection map, or empty dict."""
    if not _db_available():
        return {}

    now = time.monotonic()
    if now < _ACTIVE_MAP_CACHE["expires_at"]:
        return dict(_ACTIVE_MAP_CACHE["value"])

    async with get_async_session() as session:
        row = (await session.execute(
            select(ServerSetting.value).where(ServerSetting.key == _ACTIVE_KEY)
        )).fetchone()

    val = dict(row[0]) if row and row[0] else {}
    _ACTIVE_MAP_CACHE["value"] = val
    _ACTIVE_MAP_CACHE["expires_at"] = now + 5.0
    return dict(val)

async def set_active(kind: str, entry_id: str) -> dict:
    """Set the active pool entry for ``kind`` (chat | embedding)."""
    if kind not in _VALID_KINDS:
        raise ValueError(f"Unsupported kind '{kind}'")
    current = await get_active_map()
    current[kind] = entry_id
    stmt = (
        pg_insert(ServerSetting)
        .values(key=_ACTIVE_KEY, value=current)
        .on_conflict_do_update(index_elements=["key"], set_={"value": current})
    )
    async with get_async_session() as session:
        await session.execute(stmt)
        await session.commit()

    # Invalidate cache immediately on update
    _ACTIVE_MAP_CACHE["expires_at"] = 0.0
    _invalidate_role_pool_snapshot()
    return current

async def get_active(kind: str) -> dict | None:
    """Return the active entry (with token) for ``kind``, or None."""
    active = await get_active_map()
    entry_id = active.get(kind)
    if not entry_id:
        return None
    return await _get_entry_with_token(entry_id)

async def set_pool_entry_active(entry_id: str, enabled: bool) -> None:
    """Set the active status of a pool entry."""
    from sqlalchemy import update, func
    async with get_async_session() as session:
        stmt = (
            update(LLMPoolEntry)
            .where(LLMPoolEntry.id == entry_id)
            .values(is_active=enabled, updated_at=func.now())
        )
        await session.execute(stmt)
        await session.commit()
    invalidate_entry_cache(str(entry_id))
    _invalidate_role_pool_snapshot()
