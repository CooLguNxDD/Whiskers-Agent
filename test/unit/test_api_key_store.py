"""Unit tests for api_key_store.py."""

import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete

from db_layer.connection import get_async_session
from db_layer.models import ApiKey
from db_layer.vault import _decrypt
from db_layer.api_key_store import (
    create_api_key,
    lookup_active_by_token,
    list_api_keys,
    revoke_api_key,
    rotate_api_key,
    delete_api_key,
)


@pytest.fixture(autouse=True)
async def cleanup_api_keys():
    """Clean up api_keys database table before and after each test."""
    async with get_async_session() as session:
        await session.execute(delete(ApiKey))
        await session.commit()
    yield
    async with get_async_session() as session:
        await session.execute(delete(ApiKey))
        await session.commit()


@pytest.mark.asyncio
async def test_create_api_key_success():
    """Test that create_api_key inserts correct values and returns plaintext token."""
    res = await create_api_key(subject="test_sub", name="test_key", tenant_id=1)

    assert res["token"].startswith("octk_")
    assert res["prefix"] == res["token"][:10]
    assert res["name"] == "test_key"
    assert res["expires_at"] is None
    assert "key_id" in res

    # Verify that the encrypted value in DB decrypts back to the token
    async with get_async_session() as session:
        stmt = select(_decrypt(ApiKey.value)).where(ApiKey.key_id == res["key_id"])
        db_res = await session.execute(stmt)
        decrypted_token = db_res.scalar()
        assert decrypted_token == res["token"]


@pytest.mark.asyncio
async def test_lookup_active_by_token():
    """Test lookup_active_by_token with fresh, unknown, revoked, and expired keys."""
    # 1. Fresh key (active, not expired)
    key_info = await create_api_key(subject="test_sub", name="test_key", tenant_id=1)
    token = key_info["token"]

    lookup_res = await lookup_active_by_token(token)
    assert lookup_res is not None
    assert lookup_res["key_id"] == key_info["key_id"]
    assert lookup_res["subject"] == "test_sub"
    assert lookup_res["expires_at"] is None

    # Check that last_used_at is updated
    async with get_async_session() as session:
        stmt = select(ApiKey.last_used_at).where(ApiKey.key_id == key_info["key_id"])
        db_res = await session.execute(stmt)
        last_used = db_res.scalar()
        assert last_used is not None

    # 2. Unknown token
    assert await lookup_active_by_token("unknown_token") is None

    # 3. Revoked key
    await revoke_api_key(subject="test_sub", key_id=key_info["key_id"], tenant_id=1)
    assert await lookup_active_by_token(token) is None

    # 4. Expired key (expires_at in the past)
    expired_at = datetime.now(timezone.utc) - timedelta(hours=1)
    expired_key_info = await create_api_key(
        subject="test_sub", name="expired_key", expires_at=expired_at, tenant_id=1
    )
    assert await lookup_active_by_token(expired_key_info["token"]) is None


@pytest.mark.asyncio
async def test_revoke_and_delete():
    """Test revoke and delete operations and how they affect list_api_keys."""
    # Create key
    key_info = await create_api_key(subject="test_sub", name="test_key", tenant_id=1)
    key_id = key_info["key_id"]

    # List key, confirm it's present and active
    keys = await list_api_keys(subject="test_sub", tenant_id=1)
    assert len(keys) == 1
    assert keys[0]["key_id"] == key_id
    assert keys[0]["status"] == "active"
    assert keys[0]["revoked_at"] is None

    # Revoke key
    revoked = await revoke_api_key(subject="test_sub", key_id=key_id, tenant_id=1)
    assert revoked is True

    # Revoking again should return False
    assert await revoke_api_key(subject="test_sub", key_id=key_id, tenant_id=1) is False

    # List keys, confirm status is revoked and revoked_at is populated
    keys_after_revoke = await list_api_keys(subject="test_sub", tenant_id=1)
    assert len(keys_after_revoke) == 1
    assert keys_after_revoke[0]["status"] == "revoked"
    assert keys_after_revoke[0]["revoked_at"] is not None

    # Delete key
    deleted = await delete_api_key(subject="test_sub", key_id=key_id, tenant_id=1)
    assert deleted is True

    # Deleting again should return False
    assert await delete_api_key(subject="test_sub", key_id=key_id, tenant_id=1) is False

    # List keys, confirm row is deleted
    keys_after_delete = await list_api_keys(subject="test_sub", tenant_id=1)
    assert len(keys_after_delete) == 0


@pytest.mark.asyncio
async def test_rotate_api_key_success():
    """rotate_api_key should revoke the old key and return a fresh replacement with same name."""
    # Create original
    orig = await create_api_key(subject="test_sub", name="rotating-key", tenant_id=1)
    orig_id = orig["key_id"]
    orig_token = orig["token"]

    # Rotate
    replacement = await rotate_api_key(subject="test_sub", key_id=orig_id, tenant_id=1)
    assert replacement is not None
    assert replacement["name"] == "rotating-key"
    assert replacement["token"].startswith("octk_")
    assert replacement["token"] != orig_token
    assert "key_id" in replacement

    # Old key should be revoked in list
    keys = await list_api_keys(subject="test_sub", tenant_id=1)
    assert len(keys) == 2
    revoked = [k for k in keys if k["key_id"] == orig_id][0]
    assert revoked["status"] == "revoked"
    assert revoked["revoked_at"] is not None

    # Replacement should be active and different
    new_one = [k for k in keys if k["key_id"] == replacement["key_id"]][0]
    assert new_one["status"] == "active"
    assert new_one["key_id"] != orig_id

    # Old token no longer active
    assert await lookup_active_by_token(orig_token) is None


@pytest.mark.asyncio
async def test_rotate_api_key_not_found():
    """rotate on unknown or already-revoked key returns None."""
    key = await create_api_key(subject="test_sub", name="to-be-revoked", tenant_id=1)
    await revoke_api_key(subject="test_sub", key_id=key["key_id"], tenant_id=1)

    assert await rotate_api_key(subject="test_sub", key_id=key["key_id"], tenant_id=1) is None
    assert await rotate_api_key(subject="test_sub", key_id="ak_nonexistent", tenant_id=1) is None


@pytest.mark.asyncio
async def test_list_api_keys_properties():
    """Test list_api_keys ordering, filtering, and safety characteristics."""
    # Create keys for different subjects
    key1 = await create_api_key(subject="sub_a", name="key1", tenant_id=1)
    key2 = await create_api_key(subject="sub_a", name="key2", tenant_id=1)
    await create_api_key(subject="sub_b", name="key3", tenant_id=1)

    # List for sub_a
    keys_a = await list_api_keys(subject="sub_a", tenant_id=1)
    assert len(keys_a) == 2
    # Ordered by created_at DESC, so key2 should be first
    assert keys_a[0]["key_id"] == key2["key_id"]
    assert keys_a[1]["key_id"] == key1["key_id"]

    # Verify no plaintext or value keys are returned
    for key in keys_a:
        assert "value" not in key
        assert "token" not in key
        # Make sure no value is the full plaintext token
        for val in key.values():
            if isinstance(val, str):
                assert val != key1["token"]
                assert val != key2["token"]
                # Also ensure full 48-char token is not leaked
                assert len(val) < 48


@pytest.mark.asyncio
async def test_tenant_isolation_store():
    """Test that tenant_id strictly isolates keys across all CRUD operations."""
    from core.api_key_management.store import update_api_key_scopes

    # Key in tenant 1 and key in tenant 2 under same subject
    key_t1 = await create_api_key(subject="shared_sub", name="key_t1", tenant_id=1)
    key_t2 = await create_api_key(subject="shared_sub", name="key_t2", tenant_id=2)

    # Listing tenant 1 only returns key_t1
    keys_t1 = await list_api_keys(subject="shared_sub", tenant_id=1)
    assert len(keys_t1) == 1
    assert keys_t1[0]["key_id"] == key_t1["key_id"]

    # Listing tenant 2 only returns key_t2
    keys_t2 = await list_api_keys(subject="shared_sub", tenant_id=2)
    assert len(keys_t2) == 1
    assert keys_t2[0]["key_id"] == key_t2["key_id"]

    # Cannot mutate tenant 1 key from tenant 2
    assert await revoke_api_key("shared_sub", key_t1["key_id"], tenant_id=2) is False
    assert await rotate_api_key("shared_sub", key_t1["key_id"], tenant_id=2) is None
    assert await update_api_key_scopes("shared_sub", key_t1["key_id"], ["*"], tenant_id=2) is False
    assert await delete_api_key("shared_sub", key_t1["key_id"], tenant_id=2) is False

    # Key in tenant 1 is still active
    keys_t1_check = await list_api_keys(subject="shared_sub", tenant_id=1)
    assert len(keys_t1_check) == 1
    assert keys_t1_check[0]["status"] == "active"


