"""Unit tests for the Tenant model schema, tenant creation, and tenant/user management."""

import pytest
from sqlalchemy import select, delete

from db_layer.connection import get_async_session
from db_layer.models import User, Tenant
from core.user_management import (
    create_tenant,
    list_tenants,
    create_user,
    list_users,
)


@pytest.fixture(autouse=True)
async def cleanup_db():
    """Autouse fixture to clean up User and Tenant tables before and after each test."""
    async with get_async_session() as session:
        # Delete User first because of foreign key constraint on User.tenant_id
        await session.execute(delete(User))
        # Delete all tenants except the default one (id=1)
        await session.execute(delete(Tenant).where(Tenant.id != 1))
        
        # Idempotently ensure the default tenant (id=1) exists
        stmt = select(Tenant).where(Tenant.id == 1)
        result = await session.execute(stmt)
        default_tenant = result.scalars().first()
        if default_tenant is None:
            # Re-insert default tenant
            session.add(Tenant(id=1, name="default", slug="default"))
        await session.commit()
    yield
    async with get_async_session() as session:
        await session.execute(delete(User))
        await session.execute(delete(Tenant).where(Tenant.id != 1))
        await session.commit()


@pytest.mark.asyncio
async def test_create_tenant_happy_path():
    """Verify that create_tenant creates a tenant with the given parameters."""
    tenant = await create_tenant("Acme Corp", "acme", is_active=True)
    assert tenant["name"] == "Acme Corp"
    assert tenant["slug"] == "acme"
    assert tenant["is_active"] is True
    assert tenant["id"] is not None
    assert tenant["created_at"] is not None

    # Verify database entry exists
    async with get_async_session() as session:
        stmt = select(Tenant).where(Tenant.id == tenant["id"])
        result = await session.execute(stmt)
        db_tenant = result.scalars().first()
        assert db_tenant is not None
        assert db_tenant.name == "Acme Corp"
        assert db_tenant.slug == "acme"


@pytest.mark.asyncio
async def test_create_tenant_duplicate_name():
    """Verify create_tenant raises ValueError when tenant name already exists."""
    await create_tenant("Acme Corp", "acme")
    with pytest.raises(ValueError) as excinfo:
        await create_tenant("Acme Corp", "acme-other")
    assert "Tenant with name 'Acme Corp' or slug 'acme-other' already exists." in str(excinfo.value)


@pytest.mark.asyncio
async def test_create_tenant_duplicate_slug():
    """Verify create_tenant raises ValueError when tenant slug already exists."""
    await create_tenant("Acme Corp", "acme")
    with pytest.raises(ValueError) as excinfo:
        await create_tenant("Acme Corp Other", "acme")
    assert "Tenant with name 'Acme Corp Other' or slug 'acme' already exists." in str(excinfo.value)


@pytest.mark.asyncio
async def test_list_tenants():
    """Verify list_tenants returns tenants ordered by their ID."""
    initial_list = await list_tenants()
    # Since ID 1 is the default seeded tenant, we assert it is present
    assert len(initial_list) == 1
    assert initial_list[0]["id"] == 1
    assert initial_list[0]["name"] == "default"

    t1 = await create_tenant("Tenant A", "t-a")
    t2 = await create_tenant("Tenant B", "t-b")

    tenants = await list_tenants()
    assert len(tenants) == 3
    assert tenants[0]["id"] == 1
    assert tenants[1]["id"] == t1["id"]
    assert tenants[2]["id"] == t2["id"]
    assert tenants[1]["name"] == "Tenant A"
    assert tenants[2]["name"] == "Tenant B"


@pytest.mark.asyncio
async def test_create_user_happy_path():
    """Verify create_user creates a user with valid role and tenant_id."""
    tenant = await create_tenant("Acme Corp", "acme")
    
    user = await create_user(
        username="operator_alice",
        password="secret_password",
        role="operator",
        tenant_id=tenant["id"],
        is_active=True,
    )
    assert user["username"] == "operator_alice"
    assert user["role"] == "operator"
    assert user["tenant_id"] == tenant["id"]
    assert user["is_active"] is True
    assert "password_hash" not in user

    # Verify database entry has matching fields
    async with get_async_session() as session:
        stmt = select(User).where(User.username == "operator_alice")
        result = await session.execute(stmt)
        db_user = result.scalars().first()
        assert db_user is not None
        assert db_user.tenant_id == tenant["id"]
        assert db_user.role == "operator"
        assert db_user.password_hash.startswith("$argon2")


@pytest.mark.asyncio
async def test_create_user_invalid_role():
    """Verify create_user with invalid role raises ValueError before inserting user."""
    with pytest.raises(ValueError) as excinfo:
        await create_user(
            username="invalid_user",
            password="secret_password",
            role="super_admin",  # Invalid role
            tenant_id=None,
        )
    assert "Invalid role 'super_admin'" in str(excinfo.value)

    # Assert no user row was inserted
    async with get_async_session() as session:
        stmt = select(User)
        result = await session.execute(stmt)
        users = result.scalars().all()
        assert len(users) == 0


@pytest.mark.asyncio
async def test_create_user_duplicate_username():
    """Verify create_user raises ValueError on duplicate username."""
    await create_user(
        username="operator_bob",
        password="secret_password",
        role="operator",
    )
    with pytest.raises(ValueError) as excinfo:
        await create_user(
            username="operator_bob",
            password="secret_password_different",
            role="operator",
        )
    assert "User with username 'operator_bob' already exists." in str(excinfo.value)


@pytest.mark.asyncio
async def test_list_users():
    """Verify list_users filters by tenant_id and lists all users when tenant_id=None."""
    t1 = await create_tenant("Tenant A", "t-a")
    t2 = await create_tenant("Tenant B", "t-b")

    await create_user("alice", "pass", "operator", tenant_id=t1["id"])
    await create_user("bob", "pass", "operator", tenant_id=t2["id"])
    await create_user("charlie", "pass", "operator", tenant_id=None)

    # Query all users (tenant_id=None)
    all_users = await list_users(tenant_id=None)
    # Sorted by username: alice, bob, charlie
    assert len(all_users) == 3
    assert all_users[0]["username"] == "alice"
    assert all_users[1]["username"] == "bob"
    assert all_users[2]["username"] == "charlie"

    # Query for Tenant A (t1)
    t1_users = await list_users(tenant_id=t1["id"])
    assert len(t1_users) == 1
    assert t1_users[0]["username"] == "alice"

    # Query for Tenant B (t2)
    t2_users = await list_users(tenant_id=t2["id"])
    assert len(t2_users) == 1
    assert t2_users[0]["username"] == "bob"


@pytest.mark.asyncio
async def test_create_user_invalid_tenant_id():
    """Verify that create_user with an invalid tenant_id raises a specific ValueError."""
    with pytest.raises(ValueError) as excinfo:
        await create_user(
            username="invalid_tenant_user",
            password="secret_password",
            role="operator",
            tenant_id=9999,
        )
    assert "Tenant ID 9999 not found" in str(excinfo.value)
