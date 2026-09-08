"""API key store normalizes scopes via normalize_scopes_for_storage."""

import pytest
from sqlalchemy import delete

from db_layer.connection import get_async_session
from db_layer.models import ApiKey
from core.api_key_management.store import create_api_key, update_api_key_scopes, lookup_active_by_token


@pytest.fixture(autouse=True)
async def cleanup_api_keys():
    async with get_async_session() as session:
        await session.execute(delete(ApiKey))
        await session.commit()
    yield
    async with get_async_session() as session:
        await session.execute(delete(ApiKey))
        await session.commit()


@pytest.mark.asyncio
async def test_create_api_key_none_scopes_stores_all():
    created = await create_api_key("test_sub", "all-key", scopes=None)
    assert created["scopes"] == ["all"]
    looked = await lookup_active_by_token(created["token"])
    assert looked["scopes"] == ["all"]


@pytest.mark.asyncio
async def test_create_api_key_star_normalizes_to_all():
    created = await create_api_key("test_sub", "star-key", scopes=["*"])
    assert created["scopes"] == ["all"]


@pytest.mark.asyncio
async def test_create_api_key_plugin_scopes_verbatim():
    created = await create_api_key("test_sub", "plug-key", scopes=["plugin:x"])
    assert created["scopes"] == ["plugin:x"]


@pytest.mark.asyncio
async def test_create_api_key_empty_stays_deny_all():
    created = await create_api_key("test_sub", "deny-key", scopes=[])
    assert created["scopes"] == []


@pytest.mark.asyncio
async def test_update_api_key_scopes_normalizes():
    created = await create_api_key("test_sub", "upd-key", scopes=["plugin:x"], tenant_id=1)
    ok = await update_api_key_scopes("test_sub", created["key_id"], ["*"], tenant_id=1)
    assert ok is True
    looked = await lookup_active_by_token(created["token"])
    assert looked["scopes"] == ["all"]


@pytest.mark.asyncio
async def test_update_api_key_scopes_none_to_all():
    created = await create_api_key("test_sub", "upd2-key", scopes=["plugin:x"], tenant_id=1)
    ok = await update_api_key_scopes("test_sub", created["key_id"], None, tenant_id=1)
    assert ok is True
    looked = await lookup_active_by_token(created["token"])
    assert looked["scopes"] == ["all"]
