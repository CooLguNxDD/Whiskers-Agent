"""User management store and cryptographic utilities for Whiskers Agent.

Provides helpers to seed default admin users, query users, hash passwords with Argon2,
and verify both Argon2 (new) and legacy Bcrypt password hashes.
"""

import logging
import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db_layer.connection import get_async_session
from db_layer.models import User, Tenant
from db_layer.vault import VaultService
from utils.server_config import ROLES_CONFIG

logger = logging.getLogger("whiskers")


def _user_to_dict(u: User) -> dict:
    """Serialize a User ORM instance to a dictionary.

    Converts SQLAlchemy User model attributes into standard python types,
    serializing the created_at timestamp if present.
    """
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "tenant_id": u.tenant_id,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


async def ensure_default_admin_user() -> None:
    """Idempotently seed the default admin user using the password hash from vault.

    If the users table has no master user, it fetches the hash from VaultService for
    subject='admin' / key='password_hash' and inserts a default master admin.
    """
    async with get_async_session() as session:
        # Check if any master user exists in the database
        try:
            stmt = select(User).where(User.role == "master").limit(1)
            result = await session.execute(stmt)
            master_user = result.scalars().first()
        except Exception as exc:
            logger.warning(
                "Failed to query users table: %s. "
                "The database schema might not be fully migrated yet. "
                "Skipping default admin user seeding.",
                exc
            )
            return

        if master_user is not None:
            logger.info("Master user already exists. Skipping default admin seeding.")
            return

        # Query vault for admin credentials
        vault = VaultService()
        try:
            vault_username = await vault.get("admin", "username")
            vault_hash = await vault.get("admin", "password_hash")
        except Exception as exc:
            logger.warning(
                "Failed to query VaultService for 'admin': %s. "
                "Skipping default admin user seeding.",
                exc
            )
            return

        if not vault_hash:
            logger.warning(
                "No password hash found in vault for subject 'admin'. "
                "Skipping default admin user seeding."
            )
            return

        admin_username = vault_username or "admin"

        # Check if user already exists
        admin_stmt = select(User).where(User.username == admin_username).limit(1)
        admin_result = await session.execute(admin_stmt)
        admin_user = admin_result.scalars().first()

        if admin_user is not None:
            # Update role and password hash of existing admin user to master
            admin_user.role = "master"
            admin_user.password_hash = vault_hash
            logger.info("Updated existing '%s' user to 'master' role with vault hash.", admin_username)
        else:
            # Create a new admin user with master role
            # tenant_id=1 is the default tenant seeded by core_034; set explicitly
            # so the admin row is consistent with all other rows (core_035 backfills NULL→1).
            new_user = User(
                username=admin_username,
                role="master",
                password_hash=vault_hash,
                tenant_id=1,
            )
            session.add(new_user)
            logger.info("Created new '%s' user with 'master' role from vault hash.", admin_username)

        await session.commit()


async def get_user_by_username(username: str) -> dict | None:
    """Retrieve a user by their username and return it as a dictionary.

    Returns None if the user does not exist.
    """
    async with get_async_session() as session:
        stmt = select(User).where(User.username == username).limit(1)
        result = await session.execute(stmt)
        user = result.scalars().first()
        if user is None:
            return None
        d = _user_to_dict(user)
        d["password_hash"] = user.password_hash
        return d


def verify_password(stored_hash: str, password: str) -> bool:
    """Verify a plaintext password against a stored Argon2 or Bcrypt hash.

    Returns True if the password matches, False otherwise (including errors).
    """
    if not stored_hash or not password:
        return False

    if stored_hash.startswith("$argon2"):
        try:
            # PasswordHasher.verify returns True or raises VerifyMismatchError
            return PasswordHasher().verify(stored_hash, password)
        except VerifyMismatchError:
            return False
        except (InvalidHashError, VerificationError):
            logger.warning("verify_password: malformed argon2 hash", exc_info=True)
            return False
        except Exception:
            logger.exception("verify_password: unexpected argon2 failure")
            return False
    else:
        # Legacy bcrypt hash
        try:
            stored_bytes = stored_hash.encode("utf-8")
            password_bytes = password.encode("utf-8")
            return bcrypt.checkpw(password_bytes, stored_bytes)
        except ValueError:
            logger.warning("verify_password: malformed bcrypt hash", exc_info=True)
            return False
        except Exception:
            logger.exception("verify_password: unexpected bcrypt failure")
            return False


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2ID.

    Returns the hashed string.
    """
    return PasswordHasher().hash(password)


def _tenant_to_dict(t: Tenant) -> dict:
    """Serialize a Tenant ORM instance to a dictionary.

    Converts SQLAlchemy Tenant model attributes into standard python types,
    serializing the created_at timestamp if present.
    """
    return {
        "id": t.id,
        "name": t.name,
        "slug": t.slug,
        "is_active": t.is_active,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


async def create_tenant(name: str, slug: str, *, is_active: bool = True) -> dict:
    """Create a new tenant with the specified name and slug.

    Inserts a new Tenant record into the database, handling unique constraints
    for name and slug, and returns the serialized Tenant dictionary.
    """
    async with get_async_session() as session:
        tenant = Tenant(name=name, slug=slug, is_active=is_active)
        session.add(tenant)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise ValueError(f"Tenant with name '{name}' or slug '{slug}' already exists.")
        await session.refresh(tenant)
        return _tenant_to_dict(tenant)


async def list_tenants() -> list[dict]:
    """Retrieve all tenants ordered by their ID.

    Queries the database for all Tenant records, orders them by their primary
    key ID, and returns a list of serialized Tenant dictionaries.
    """
    async with get_async_session() as session:
        stmt = select(Tenant).order_by(Tenant.id)
        result = await session.execute(stmt)
        tenants = result.scalars().all()
        return [_tenant_to_dict(t) for t in tenants]


async def create_user(
    username: str,
    password: str,
    role: str,
    tenant_id: int | None = None,
    *,
    is_active: bool = True,
) -> dict:
    """Create a new user with password hashing and role validation.

    Validates that the role is supported, hashes the password using Argon2, and
    inserts the User record under the specified tenant.
    """
    if role not in ROLES_CONFIG:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {sorted(ROLES_CONFIG.keys())}")

    hashed = hash_password(password)
    async with get_async_session() as session:
        if tenant_id is not None:
            stmt = select(Tenant).where(Tenant.id == tenant_id)
            result = await session.execute(stmt)
            if result.scalars().first() is None:
                raise ValueError(f"Tenant ID {tenant_id} not found.")

        user = User(
            username=username,
            password_hash=hashed,
            role=role,
            tenant_id=tenant_id,
            is_active=is_active,
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise ValueError(f"User with username '{username}' already exists.")
        await session.refresh(user)
        return _user_to_dict(user)


async def list_users(tenant_id: int | None = None) -> list[dict]:
    """Retrieve users, optionally filtering by tenant.

    Queries the database for User records, optionally filters them by the given
    tenant ID, orders them by username, and returns a list of serialized User dictionaries.
    """
    async with get_async_session() as session:
        stmt = select(User)
        if tenant_id is not None:
            stmt = stmt.where(User.tenant_id == tenant_id)
        stmt = stmt.order_by(User.username)
        result = await session.execute(stmt)
        users = result.scalars().all()
        return [_user_to_dict(u) for u in users]


async def update_user_password(username: str, new_password: str) -> bool:
    """Update password for an existing user in both the users table and the Vault."""
    if not new_password or not new_password.strip():
        raise ValueError("Password cannot be empty.")

    hashed = hash_password(new_password)
    user_role = None
    async with get_async_session() as session:
        stmt = select(User).where(User.username == username).limit(1)
        result = await session.execute(stmt)
        user = result.scalars().first()
        if user is None:
            return False
        user_role = user.role
        user.password_hash = hashed
        await session.commit()

    # Synchronize with Vault if admin or master user
    try:
        vault = VaultService()
        vault_admin = await vault.get("admin", "username")
        if vault_admin == username or user_role == "master":
            await vault.set("admin", "password_hash", hashed)
    except Exception as exc:
        logger.warning("Failed to sync updated password hash to Vault: %s", exc)

    return True


async def update_user_username(old_username: str, new_username: str) -> bool:
    """Update username for an existing user in both the users table and the Vault."""
    if not new_username or not new_username.strip():
        raise ValueError("Username cannot be empty.")

    user_role = None
    async with get_async_session() as session:
        stmt = select(User).where(User.username == old_username).limit(1)
        result = await session.execute(stmt)
        user = result.scalars().first()
        if user is None:
            return False
        user_role = user.role
        user.username = new_username.strip()
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise ValueError(f"User with username '{new_username}' already exists.")

    # Synchronize with Vault if admin or master user
    try:
        vault = VaultService()
        vault_admin = await vault.get("admin", "username")
        if vault_admin == old_username or user_role == "master":
            await vault.set("admin", "username", new_username.strip())
    except Exception as exc:
        logger.warning("Failed to sync updated username to Vault: %s", exc)

    return True
