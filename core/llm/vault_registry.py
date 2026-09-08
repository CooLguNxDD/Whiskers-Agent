import time
from sqlalchemy import select
from db_layer.connection import get_async_session
from db_layer.models import LLMPoolEntry
from db_layer.vault import _decrypt, _encrypt

def encrypt_api_key(value: str):
    """Encrypt an API key string for secure storage in LLMPoolEntry."""
    return _encrypt(value)

def decrypt_api_key_column(column):
    """Generate SQL expression to decrypt an encrypted API key column."""
    return _decrypt(column)

def invalidate_entry_cache(entry_id: str) -> None:
    """Evict a specific LLM pool entry from the local in-memory TTL cache."""
    _ENTRY_CACHE.pop(str(entry_id), None)

_ENTRY_CACHE: dict[str, dict] = {}
_ENTRY_CACHE_TTL = 5.0
async def _get_entry_with_token(entry_id: str) -> dict | None:
    """Return a single pool entry including its decrypted token."""
    entry_id = str(entry_id)
    now = time.monotonic()
    cached = _ENTRY_CACHE.get(entry_id)
    if cached and now < cached["expires_at"]:
        return dict(cached["value"]) if cached["value"] is not None else None

    async with get_async_session() as session:
        row = (await session.execute(
            select(
                LLMPoolEntry.id,
                LLMPoolEntry.provider,
                LLMPoolEntry.model,
                LLMPoolEntry.dimensions,
                LLMPoolEntry.base_url,
                LLMPoolEntry.strength,
                LLMPoolEntry.is_active,
                decrypt_api_key_column(LLMPoolEntry.api_key).label("api_key"),
            ).where(LLMPoolEntry.id == entry_id)
        )).fetchone()

    if not row or not row.is_active:
        _ENTRY_CACHE[entry_id] = {"value": None, "expires_at": now + _ENTRY_CACHE_TTL}
        return None

    val = {
        "id": str(row.id),
        "provider": row.provider,
        "model": row.model,
        "dimensions": row.dimensions,
        "base_url": row.base_url,
        "strength": row.strength,
        "api_key": row.api_key,
    }
    _ENTRY_CACHE[entry_id] = {"value": val, "expires_at": now + _ENTRY_CACHE_TTL}
    return dict(val)
