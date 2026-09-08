"""
VaultService — Layer 2 static credential store backed by pgcrypto.

All values are encrypted at rest using pgp_sym_encrypt with the session-level
GUC ``app.master_key`` (injected on every DB connection by db_layer/connection.py).

Plaintext only crosses the Python boundary at:
  - ``set()``  — caller supplies the plaintext value
  - ``get()``  — decrypted value is returned to the caller

Nothing is ever logged or persisted in plaintext.
"""

import logging
from typing import Sequence

from sqlalchemy import String, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db_layer.connection import get_async_session
from db_layer.models import PluginCredential

logger = logging.getLogger("whiskers")


def _encrypt(value: str):
    """Return a pgp_sym_encrypt expression using the session-level master key GUC."""
    return func.pgp_sym_encrypt(value, func.current_setting("app.master_key"))


def _decrypt(column):
    """Return a pgp_sym_decrypt expression cast to String."""
    return func.pgp_sym_decrypt(column, func.current_setting("app.master_key")).cast(String)


class MissingCredentialError(Exception):
    """Raised when a required vault credential is absent."""

    def __init__(self, plugin_id: str, key_name: str) -> None:
        """Initialize the missing credential error with context details."""
        self.plugin_id = plugin_id
        self.key_name = key_name
        super().__init__(
            f"Plugin '{plugin_id}' is missing required credential '{key_name}'. "
            f"Fix with:  whiskers vault set {plugin_id} {key_name} <value>"
        )


class VaultService:
    """pgcrypto-encrypted credential store for plugin static credentials."""

    # ------------------------------------------------------------------ write

    async def set(self, plugin_id: str, key_name: str, value: str) -> None:
        """Upsert an encrypted credential for ``plugin_id / key_name``."""
        stmt = (
            pg_insert(PluginCredential)
            .values(
                plugin_id=plugin_id,
                key_name=key_name,
                value=_encrypt(value),
                created_at=func.now(),
                updated_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=["plugin_id", "key_name"],
                set_={
                    "value": _encrypt(value),
                    "updated_at": func.now(),
                },
            )
        )
        async with get_async_session() as session:
            await session.execute(stmt)
            await session.commit()

    # ------------------------------------------------------------------ read

    async def get(self, plugin_id: str, key_name: str) -> str | None:
        """Return the decrypted credential value, or ``None`` if absent."""
        stmt = select(_decrypt(PluginCredential.value)).where(
            PluginCredential.plugin_id == plugin_id,
            PluginCredential.key_name == key_name,
        )
        async with get_async_session() as session:
            row = (await session.execute(stmt)).fetchone()
            return row[0] if row else None

    async def get_required(self, plugin_id: str, key_name: str) -> str:
        """Return the decrypted value or raise ``MissingCredentialError``."""
        value = await self.get(plugin_id, key_name)
        if value is None:
            raise MissingCredentialError(plugin_id, key_name)
        return value

    async def exists(self, plugin_id: str, key_name: str) -> bool:
        """Return ``True`` if the credential row exists (no decrypt needed)."""
        stmt = select(PluginCredential.id).where(
            PluginCredential.plugin_id == plugin_id,
            PluginCredential.key_name == key_name,
        ).limit(1)
        async with get_async_session() as session:
            return (await session.execute(stmt)).fetchone() is not None

    async def list_keys(self, plugin_id: str) -> list[str]:
        """Return the key names stored for ``plugin_id`` (names are plaintext)."""
        stmt = (
            select(PluginCredential.key_name)
            .where(PluginCredential.plugin_id == plugin_id)
            .order_by(PluginCredential.key_name)
        )
        async with get_async_session() as session:
            return [r[0] for r in (await session.execute(stmt)).fetchall()]

    async def get_all_present_keys(self) -> dict[str, list[str]]:
        """Return all present keys for all plugins."""
        stmt = select(PluginCredential.plugin_id, PluginCredential.key_name).order_by(
            PluginCredential.plugin_id, PluginCredential.key_name
        )
        async with get_async_session() as session:
            rows = (await session.execute(stmt)).fetchall()
            result: dict[str, list[str]] = {}
            for plugin_id, key_name in rows:
                if plugin_id not in result:
                    result[plugin_id] = []
                result[plugin_id].append(key_name)
            return result

    # ------------------------------------------------------------------ delete

    async def delete(self, plugin_id: str, key_name: str) -> None:
        """Delete a single credential row."""
        stmt = delete(PluginCredential).where(
            PluginCredential.plugin_id == plugin_id,
            PluginCredential.key_name == key_name,
        )
        async with get_async_session() as session:
            await session.execute(stmt)
            await session.commit()

    async def delete_plugin(self, plugin_id: str) -> None:
        """Delete all credential rows for ``plugin_id``."""
        stmt = delete(PluginCredential).where(
            PluginCredential.plugin_id == plugin_id,
        )
        async with get_async_session() as session:
            await session.execute(stmt)
            await session.commit()

    # ------------------------------------------------------------------ helper

    async def missing_keys(self, plugin_id: str, required: Sequence[str]) -> list[str]:
        """Return the subset of ``required`` keys that are not yet stored."""
        if not required:
            return []
        stmt = select(PluginCredential.key_name).where(
            PluginCredential.plugin_id == plugin_id,
            PluginCredential.key_name.in_(required),
        )
        async with get_async_session() as session:
            found = {r[0] for r in (await session.execute(stmt)).fetchall()}
        return [k for k in required if k not in found]
