"""User management package for Whiskers Agent.

Exposes functions to seed, query, and verify/hash credentials for users,
and manage multi-tenant workspaces.
"""

from core.user_management.store import (
    ensure_default_admin_user,
    get_user_by_username,
    verify_password,
    hash_password,
    create_tenant,
    list_tenants,
    create_user,
    list_users,
)

__all__ = [
    "ensure_default_admin_user",
    "get_user_by_username",
    "verify_password",
    "hash_password",
    "create_tenant",
    "list_tenants",
    "create_user",
    "list_users",
]
