"""Database layer — SQLAlchemy models, engine, and session factory."""

from .cache import cache_get, cache_set
from .connection import get_async_session, init_db
from .oauth_store import db_delete_token, db_load_client, db_load_token, db_save_client, db_save_token
from .vault import VaultService, MissingCredentialError
from .plugin_registry_store import DBPluginRegistry, PluginRecord, PluginNotFoundError, PluginLoadError

__all__ = [
    "get_async_session",
    "init_db",
    "cache_get",
    "cache_set",
    "db_save_client",
    "db_load_client",
    "db_save_token",
    "db_load_token",
    "db_delete_token",
    # Layer 2 services
    "VaultService",
    "MissingCredentialError",
    "DBPluginRegistry",
    "PluginRecord",
    "PluginNotFoundError",
    "PluginLoadError",
]
