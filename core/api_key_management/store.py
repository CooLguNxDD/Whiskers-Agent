"""core.api_key_management.store — Encrypted CRUD for API keys.

Provides create, lookup, list, revoke, rotate (revoke+create replacement), and delete
operations for API keys with encryption at rest utilizing the database pgcrypto extension.
"""

import hashlib
import logging
import secrets
from datetime import datetime

from sqlalchemy import select, update, delete, func

from db_layer.connection import get_async_session
from db_layer.models import ApiKey, ApiKeyScopePreset
from db_layer.vault import _encrypt, _decrypt

logger = logging.getLogger("whiskers")


async def create_api_key(
    subject: str,
    name: str,
    expires_at: datetime | None = None,
    scopes: list[str] | None = None,
    tenant_id: int | None = None,
) -> dict:
    """Create a new API key with the given subject, name, expiration, and scopes.

    Scopes are normalized via ``normalize_scopes_for_storage``:
    ``None`` (UI all-selected) → ``["all"]``; lists containing ``all``/``*`` →
    ``["all"]``; ``[]`` stays deny-all. Generate a secure ``octk_`` token.
    """
    from core.scope_management.sentinels import normalize_scopes_for_storage

    scopes = normalize_scopes_for_storage(scopes)

    token = "octk_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    prefix = token[:10]
    key_id = "ak_" + secrets.token_hex(8)

    async with get_async_session() as session:
        api_key = ApiKey(
            key_id=key_id,
            subject=subject,
            name=name,
            prefix=prefix,
            token_hash=token_hash,
            value=_encrypt(token),
            status="active",
            expires_at=expires_at,
            scopes=scopes,
            tenant_id=tenant_id,
        )
        session.add(api_key)
        await session.commit()

    return {
        "key_id": key_id,
        "token": token,
        "prefix": prefix,
        "name": name,
        "expires_at": expires_at,
        "scopes": scopes,
        "tenant_id": tenant_id,
    }


async def lookup_active_by_token(token: str) -> dict | None:
    """Lookup an active API key by its token value.

    Return the key details (including `scopes`) if it exists, is active, and
    is not expired. Also trigger a best-effort update of last_used_at.
    """
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with get_async_session() as session:
        stmt = select(
            ApiKey.key_id, ApiKey.subject, ApiKey.expires_at, ApiKey.scopes, ApiKey.tenant_id,
        ).where(
            ApiKey.token_hash == token_hash,
            ApiKey.status == "active",
            (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > func.now()),
        )
        res = await session.execute(stmt)
        row = res.fetchone()
        if not row:
            return None
        key_id, subject, expires_at, scopes, tenant_id = row

    try:
        async with get_async_session() as update_session:
            update_stmt = (
                update(ApiKey)
                .where(ApiKey.key_id == key_id)
                .values(last_used_at=func.now())
            )
            await update_session.execute(update_stmt)
            await update_session.commit()
    except Exception as e:
        logger.warning(
            "Failed to update last_used_at for key_id %s: %s", key_id, e
        )

    return {
        "key_id": key_id,
        "subject": subject,
        "expires_at": expires_at,
        "scopes": scopes,
        "tenant_id": tenant_id,
    }


async def list_api_keys(subject: str, tenant_id: int) -> list[dict]:
    """List all API keys for a given subject and tenant ordered by created_at desc.

    Requires an explicit ``tenant_id`` to prevent cross-tenant key leaks.
    The returned dictionaries omit secret/value fields.
    """
    # Select metadata fields only, omitting the encrypted value column
    stmt = (
        select(
            ApiKey.key_id,
            ApiKey.name,
            ApiKey.prefix,
            ApiKey.status,
            ApiKey.expires_at,
            ApiKey.last_used_at,
            ApiKey.created_at,
            ApiKey.revoked_at,
            ApiKey.scopes,
            ApiKey.tenant_id,
        )
        .where(ApiKey.subject == subject, ApiKey.tenant_id == tenant_id)
        .order_by(ApiKey.created_at.desc())
    )

    async with get_async_session() as session:
        res = await session.execute(stmt)
        rows = res.fetchall()

    return [
        {
            "key_id": r.key_id,
            "name": r.name,
            "prefix": r.prefix,
            "status": r.status,
            "expires_at": r.expires_at,
            "last_used_at": r.last_used_at,
            "created_at": r.created_at,
            "revoked_at": r.revoked_at,
            "scopes": r.scopes,
            "tenant_id": r.tenant_id,
        }
        for r in rows
    ]


async def revoke_api_key(
    subject: str, key_id: str, tenant_id: int
) -> bool:
    """Revoke an active API key for the given subject and tenant.

    Requires an explicit ``tenant_id`` for cross-tenant isolation.
    Return True if the key was successfully revoked, False otherwise.
    """
    async with get_async_session() as session:
        # Update key status to revoked and set revoked_at timestamp
        stmt = (
            update(ApiKey)
            .where(
                ApiKey.subject == subject,
                ApiKey.key_id == key_id,
                ApiKey.status != "revoked",
                ApiKey.tenant_id == tenant_id,
            )
            .values(status="revoked", revoked_at=func.now())
        )
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount > 0


async def rotate_api_key(
    subject: str, key_id: str, tenant_id: int
) -> dict | None:
    """Revoke the key (if active) and create+return a replacement using the same name.

    Requires an explicit ``tenant_id`` for cross-tenant isolation.
    The replacement is created without an expiration (long-lived admin credential).
    Returns the same dict shape as create_api_key (including plaintext token) on success,
    or None if the key was not found / not active / could not be revoked.
    """
    # Locate the target active key to capture its name/scopes for the replacement
    async with get_async_session() as session:
        stmt = select(ApiKey.name, ApiKey.scopes, ApiKey.tenant_id).where(
            ApiKey.subject == subject,
            ApiKey.key_id == key_id,
            ApiKey.status == "active",
            ApiKey.tenant_id == tenant_id,
        )
        res = await session.execute(stmt)
        old = res.first()
    if not old:
        return None

    revoked = await revoke_api_key(subject, key_id, tenant_id=tenant_id)
    if not revoked:
        return None

    return await create_api_key(
        subject, old.name, scopes=old.scopes, tenant_id=old.tenant_id
    )


async def delete_api_key(
    subject: str, key_id: str, tenant_id: int
) -> bool:
    """Delete an API key from the database for the given subject and tenant.

    Requires an explicit ``tenant_id`` for cross-tenant isolation.
    Return True if a row was deleted, False otherwise.
    """
    async with get_async_session() as session:
        stmt = delete(ApiKey).where(
            ApiKey.subject == subject,
            ApiKey.key_id == key_id,
            ApiKey.tenant_id == tenant_id,
        )
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount > 0


async def update_api_key_scopes(
    subject: str,
    key_id: str,
    scopes: list[str] | None,
    tenant_id: int,
) -> bool:
    """Update scopes of an active/revoked API key for the given subject and tenant.

    Scopes are normalized (None/all/* → ``["all"]``). Requires an explicit ``tenant_id``
    for cross-tenant isolation. Return True if updated.
    """
    from core.scope_management.sentinels import normalize_scopes_for_storage

    scopes = normalize_scopes_for_storage(scopes)
    async with get_async_session() as session:
        stmt = (
            update(ApiKey)
            .where(
                ApiKey.subject == subject,
                ApiKey.key_id == key_id,
                ApiKey.tenant_id == tenant_id,
            )
            .values(scopes=scopes)
        )
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount > 0


async def create_scope_preset(subject: str, name: str, scopes: list[str] | None) -> dict:
    """Create a saved scope preset for the given subject.

    `scopes=None` stores NULL (legacy full access), `scopes=[]` stores an empty list
    (deny all), matching the api_keys.scopes convention.
    """
    async with get_async_session() as session:
        preset = ApiKeyScopePreset(subject=subject, name=name, scopes=scopes)
        session.add(preset)
        await session.commit()
        await session.refresh(preset)

    return {
        "id": str(preset.id),
        "name": preset.name,
        "scopes": preset.scopes,
        "created_at": preset.created_at,
    }


async def list_scope_presets(subject: str) -> list[dict]:
    """List all saved scope presets for a given subject ordered by created_at desc."""
    stmt = (
        select(ApiKeyScopePreset.id, ApiKeyScopePreset.name, ApiKeyScopePreset.scopes, ApiKeyScopePreset.created_at)
        .where(ApiKeyScopePreset.subject == subject)
        .order_by(ApiKeyScopePreset.created_at.desc())
    )

    async with get_async_session() as session:
        res = await session.execute(stmt)
        rows = res.fetchall()

    return [
        {"id": str(r.id), "name": r.name, "scopes": r.scopes, "created_at": r.created_at}
        for r in rows
    ]


async def delete_scope_preset(subject: str, preset_id: str) -> bool:
    """Delete a saved scope preset for the given subject.

    Return True if a row was deleted, False otherwise.
    """
    async with get_async_session() as session:
        stmt = delete(ApiKeyScopePreset).where(
            ApiKeyScopePreset.subject == subject, ApiKeyScopePreset.id == preset_id
        )
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount > 0

