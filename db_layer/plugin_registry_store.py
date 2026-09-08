"""
DBPluginRegistry — DB-backed plugin record store.

Distinct from the in-memory ``core.plugin_loader.plugin_registry.PluginRegistry``
lifecycle manager.  This class owns the ``plugins`` table: registering manifests,
querying active plugins, deactivating plugins, and validating that all required
credentials are present in the vault.

Naming note: the name "PluginRegistry" is already taken by the in-memory
lifecycle class (core/plugin_loader/plugin_registry.py).  This class is named
``DBPluginRegistry`` to avoid collision.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import text

from db_layer.connection import get_async_session
from db_layer.vault import VaultService

logger = logging.getLogger("whiskers")


class PluginMetaKey(str, Enum):
    """Metadata keys stored inside PluginRecord.meta JSONB.

    Note: content_hash lives on the dedicated ``plugins.content_hash`` column
    (core_037), not in meta. ``version_history`` remains in meta as a cap-20 log.
    """
    VERSION_HISTORY = "version_history"
    SCOPES = "scopes"
    SCOPES_HASH = "scopes_hash"
    STALE = "stale"
    STALE_SINCE = "stale_since"
    # Fail-closed schema migration error message (plugin_schema_migrator).
    MIGRATION_ERROR = "migration_error"


# Shared SELECT list — keep in sync with ``_row_to_record``.
_PLUGIN_SELECT_COLS = """
    id, display_name, version, capabilities,
    required_credentials, external_oauth_providers, meta,
    is_active, registered_at, last_seen_at, content_hash
"""


class PluginNotFoundError(Exception):
    """Raised when a plugin_id is not found in the plugins table."""


class PluginLoadError(Exception):
    """Raised when a plugin cannot load due to missing credentials."""


@dataclass
class PluginRecord:
    """
    Record representation for registered plugins.
    """
    id: str
    display_name: str
    version: str
    capabilities: list[str] = field(default_factory=list)
    required_credentials: list[str] = field(default_factory=list)
    external_oauth_providers: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    registered_at: datetime | None = None
    last_seen_at: datetime | None = None
    # Full source-tree or proxy-identity hash (``sha256:<hex>``); column-backed.
    content_hash: str | None = None


class DBPluginRegistry:
    """DB-backed store for plugin manifests (``plugins`` table)."""

    async def register(self, manifest: dict) -> None:
        """Upsert a plugin record from its resolved manifest dict."""
        plugin_id = manifest.get("name", "")
        if not plugin_id:
            raise ValueError("manifest must contain a 'name' field")

        # Preserve user-managed / loader-managed meta keys across re-registration.
        # Without this splice, every boot clobbers scopes/scopes_hash/
        # version_history/stale flags (and skills). content_hash is a real column
        # and is intentionally left untouched by register()'s ON CONFLICT.
        existing_skills: dict[str, str] = {}
        preserved_meta: dict[str, Any] = {}
        try:
            existing = await self.get(plugin_id)
            if existing and existing.meta:
                existing_skills = dict(existing.meta.get("skills") or {})
                for key in PluginMetaKey:
                    if key.value in existing.meta:
                        preserved_meta[key.value] = existing.meta[key.value]
                # Strip any legacy meta content_hash so the column remains SoT.
                preserved_meta.pop("content_hash", None)
        except Exception as exc:
            logger.debug("DBPluginRegistry.register: existing-meta lookup for %s failed (first-time or DB hiccup): %s", plugin_id, exc)

        async with get_async_session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO plugins (
                        id, display_name, version, capabilities,
                        required_credentials, external_oauth_providers, meta,
                        is_active, registered_at, last_seen_at
                    ) VALUES (
                        :id, :display_name, :version, CAST(:capabilities AS TEXT[]),
                        CAST(:required_credentials AS TEXT[]), CAST(:external_oauth_providers AS TEXT[]),
                        CAST(:meta AS JSONB), true, now(), now()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        display_name             = EXCLUDED.display_name,
                        version                  = EXCLUDED.version,
                        capabilities             = EXCLUDED.capabilities,
                        required_credentials     = EXCLUDED.required_credentials,
                        external_oauth_providers = EXCLUDED.external_oauth_providers,
                        meta                     = EXCLUDED.meta,
                        last_seen_at             = now()
                        -- is_active intentionally NOT reset here because a manually
                        -- disabled plugin (is_active=false) must stay disabled
                        -- across restarts/re-registration. New rows default to
                        -- true via the INSERT above.
                    """
                ),
                {
                    "id": plugin_id,
                    "display_name": manifest.get("display_name", plugin_id),
                    "version": manifest.get("version", "0.0.0"),
                    "capabilities": _text_array_or_empty(manifest.get("capabilities")),
                    "required_credentials": _credential_keys_or_empty(
                        manifest.get("required_credentials")
                    ),
                    "external_oauth_providers": _oauth_provider_names(
                        manifest.get("external_oauth")
                    ),
                    "meta": _json_or_empty_object({
                        **manifest.get("meta", {}),
                        "layer2_oauth_enabled": bool(manifest.get("layer2_oauth_enabled", manifest.get("enable_oauth", False))),
                        "tier": manifest.get("tier", "free"),
                        "description": manifest.get("description", ""),
                        # Preserve (do not clobber) previously saved skills from DB/UI.
                        # New FS skills for declared keys are seeded post-register if absent.
                        "skills": existing_skills,
                        # Carried keys win so loader-persisted state survives register().
                        **preserved_meta,
                    }),
                },
            )
            await session.commit()
        logger.info(
            "DBPluginRegistry: upserted plugin '%s' v%s",
            plugin_id,
            manifest.get("version", "?"),
        )

    async def get(self, plugin_id: str) -> PluginRecord | None:
        """Return the ``PluginRecord`` for ``plugin_id``, or ``None``."""
        async with get_async_session() as session:
            row = await session.execute(
                text(
                    f"""
                    SELECT {_PLUGIN_SELECT_COLS}
                    FROM   plugins
                    WHERE  id = :id
                    """
                ),
                {"id": plugin_id},
            )
            rec = row.fetchone()
            if rec is None:
                return None
            return _row_to_record(rec)

    async def get_by_id(self, plugin_id: str) -> PluginRecord | None:
        """Alias for get(plugin_id)."""
        return await self.get(plugin_id)

    async def get_active(self) -> list[PluginRecord]:
        """Return all active plugin records."""
        async with get_async_session() as session:
            rows = await session.execute(
                text(
                    f"""
                    SELECT {_PLUGIN_SELECT_COLS}
                    FROM   plugins
                    WHERE  is_active = true
                    ORDER  BY id
                    """
                )
            )
            return [_row_to_record(r) for r in rows.fetchall()]

    async def get_inactive_ids(self) -> set[str]:
        """Return the set of plugin ids marked ``is_active = false``.

        Consumed by the loader to exclude disabled plugins from the resolved
        plan so they are never imported, registered, or initialized.
        """
        async with get_async_session() as session:
            rows = await session.execute(
                text("SELECT id FROM plugins WHERE is_active = false")
            )
            return {r[0] for r in rows.fetchall()}

    async def get_all(self) -> list[PluginRecord]:
        """Return all plugin records regardless of is_active."""
        async with get_async_session() as session:
            rows = await session.execute(
                text(
                    f"""
                    SELECT {_PLUGIN_SELECT_COLS}
                    FROM   plugins
                    ORDER  BY id
                    """
                )
            )
            return [_row_to_record(r) for r in rows.fetchall()]

    async def deactivate(self, plugin_id: str) -> None:
        """Mark a plugin as inactive (soft delete)."""
        async with get_async_session() as session:
            await session.execute(
                text(
                    "UPDATE plugins SET is_active = false WHERE id = :id"
                ),
                {"id": plugin_id},
            )
            await session.commit()

    async def set_active(self, plugin_id: str, is_active: bool) -> None:
        """Set is_active for a plugin by id."""
        async with get_async_session() as session:
            await session.execute(
                text("UPDATE plugins SET is_active = :val WHERE id = :id"),
                {"id": plugin_id, "val": is_active},
            )
            await session.commit()

    async def get_skills(self, plugin_id: str) -> dict[str, str]:
        """Return map of skill_key (relative path) -> content for the plugin (from meta.skills)."""
        record = await self.get(plugin_id)
        if record is None or not record.meta:
            return {}
        return dict(record.meta.get("skills") or {})

    async def set_skill(self, plugin_id: str, key: str, content: str) -> None:
        """Set (or overwrite) a single skill file's content under meta.skills[key]."""
        if not plugin_id or not key:
            return
        record = await self.get(plugin_id)
        if record is None:
            # plugin must be registered first
            logger.warning("set_skill: plugin %s not in DB yet; skipping", plugin_id)
            return
        new_meta = dict(record.meta or {})
        skills = dict(new_meta.get("skills") or {})
        skills[key] = content or ""
        new_meta["skills"] = skills
        async with get_async_session() as session:
            await session.execute(
                text("UPDATE plugins SET meta = CAST(:meta AS JSONB), last_seen_at = now() WHERE id = :id"),
                {"meta": json.dumps(new_meta), "id": plugin_id},
            )
            await session.commit()
        logger.info("plugin skills: set %s/%s (%d chars)", plugin_id, key, len(content or ""))

    async def delete_skill(self, plugin_id: str, key: str) -> None:
        """Remove a skill key from meta.skills (no-op if absent)."""
        if not plugin_id or not key:
            return
        record = await self.get(plugin_id)
        if record is None:
            return
        new_meta = dict(record.meta or {})
        skills = dict(new_meta.get("skills") or {})
        if key in skills:
            skills.pop(key)
            new_meta["skills"] = skills
            async with get_async_session() as session:
                await session.execute(
                    text("UPDATE plugins SET meta = CAST(:meta AS JSONB), last_seen_at = now() WHERE id = :id"),
                    {"meta": json.dumps(new_meta), "id": plugin_id},
                )
                await session.commit()
            logger.info("plugin skills: deleted %s/%s", plugin_id, key)

    async def get_scopes(self, plugin_id: str) -> tuple[list[dict], str]:
        """Return (scope entries, fingerprint) persisted under meta.scopes / meta.scopes_hash."""
        record = await self.get(plugin_id)
        if record is None or not record.meta:
            return [], ""
        return list(record.meta.get(PluginMetaKey.SCOPES.value) or []), str(record.meta.get(PluginMetaKey.SCOPES_HASH.value) or "")

    async def set_scopes(self, plugin_id: str, entries: list[dict], fingerprint: str) -> None:
        """Persist scope entries + fingerprint under meta.scopes / meta.scopes_hash.

        Used to seed the in-memory PermissionRegistry across restarts before a
        plugin lazy-loads, and to detect a manifest scope change on hot-swap
        (fingerprint mismatch → resync) — mirrors the meta.skills pattern.
        """
        if not plugin_id:
            return
        record = await self.get(plugin_id)
        if record is None:
            logger.warning("set_scopes: plugin %s not in DB yet; skipping", plugin_id)
            return
        new_meta = dict(record.meta or {})
        new_meta[PluginMetaKey.SCOPES.value] = entries
        new_meta[PluginMetaKey.SCOPES_HASH.value] = fingerprint
        async with get_async_session() as session:
            await session.execute(
                text("UPDATE plugins SET meta = CAST(:meta AS JSONB), last_seen_at = now() WHERE id = :id"),
                {"meta": json.dumps(new_meta), "id": plugin_id},
            )
            await session.commit()
        logger.info("plugin scopes: persisted %s (%d tokens, hash=%s)", plugin_id, len(entries), fingerprint[:8])

    async def get_config_override(self, plugin_id: str) -> dict[str, Any] | None:
        """Return the console-edited config.json override (meta.config), or None if unset."""
        record = await self.get(plugin_id)
        if record is None or not record.meta:
            return None
        override = record.meta.get("config")
        return dict(override) if isinstance(override, dict) else None

    async def set_config_override(self, plugin_id: str, config: dict[str, Any]) -> None:
        """Set (overwrite) the console-edited config.json override under meta.config."""
        if not plugin_id:
            return
        record = await self.get(plugin_id)
        if record is None:
            logger.warning("set_config_override: plugin %s not in DB yet; skipping", plugin_id)
            return
        new_meta = dict(record.meta or {})
        new_meta["config"] = config
        async with get_async_session() as session:
            await session.execute(
                text("UPDATE plugins SET meta = CAST(:meta AS JSONB), last_seen_at = now() WHERE id = :id"),
                {"meta": json.dumps(new_meta), "id": plugin_id},
            )
            await session.commit()
        logger.info("plugin config: set override for %s (%d keys)", plugin_id, len(config or {}))

    async def clear_config_override(self, plugin_id: str) -> None:
        """Remove the config override, reverting to the on-disk config.json (no-op if unset)."""
        record = await self.get(plugin_id)
        if record is None or not record.meta or "config" not in record.meta:
            return
        new_meta = dict(record.meta or {})
        new_meta.pop("config", None)
        async with get_async_session() as session:
            await session.execute(
                text("UPDATE plugins SET meta = CAST(:meta AS JSONB), last_seen_at = now() WHERE id = :id"),
                {"meta": json.dumps(new_meta), "id": plugin_id},
            )
            await session.commit()
        logger.info("plugin config: cleared override for %s", plugin_id)

    async def set_content_hash(self, plugin_id: str, content_hash: str, version: str) -> None:
        """Persist ``plugins.content_hash`` column + append meta.version_history.

        Clears ``stale`` / ``stale_since`` (just-loaded ≠ stale). Strips any
        legacy ``meta.content_hash`` so the column is the single source of truth.
        """
        if not plugin_id or not content_hash:
            return
        record = await self.get(plugin_id)
        if record is None:
            logger.warning("set_content_hash: plugin %s not in DB yet; skipping", plugin_id)
            return
        from core.plugin_loader.content_hash import append_version_history

        new_meta = dict(record.meta or {})
        new_meta.pop("content_hash", None)  # column is SoT (core_037)
        new_meta = append_version_history(new_meta, version or "0.0.0", content_hash, cap=20)
        new_meta.pop(PluginMetaKey.STALE.value, None)
        new_meta.pop(PluginMetaKey.STALE_SINCE.value, None)
        async with get_async_session() as session:
            await session.execute(
                text(
                    """
                    UPDATE plugins
                    SET content_hash = :content_hash,
                        meta = CAST(:meta AS JSONB),
                        last_seen_at = now()
                    WHERE id = :id
                    """
                ),
                {
                    "content_hash": content_hash,
                    "meta": json.dumps(new_meta),
                    "id": plugin_id,
                },
            )
            await session.commit()
        logger.info(
            "plugin content_hash: persisted %s (hash=%s)",
            plugin_id,
            content_hash[:16],
        )

    async def mark_stale(self, plugin_id: str) -> bool:
        """Flag a plugins row as stale (ghost). Idempotent; preserves is_active.

        Returns True when a write occurred (transition into stale).
        """
        if not plugin_id:
            return False
        ts = datetime.now(timezone.utc).isoformat()
        async with get_async_session() as session:
            result = await session.execute(
                text(
                    f"UPDATE plugins "
                    f"SET meta = jsonb_set(jsonb_set(COALESCE(meta, '{{}}'::jsonb), '{{{PluginMetaKey.STALE.value}}}', 'true'), '{{{PluginMetaKey.STALE_SINCE.value}}}', to_jsonb(CAST(:ts AS TEXT))) "
                    f"WHERE id = :id AND (meta IS NULL OR NOT (meta ? :stale_key) OR (meta->>:stale_key) != 'true')"
                ),
                {"id": plugin_id, "ts": ts, "stale_key": PluginMetaKey.STALE.value},
            )
            await session.commit()
        written = bool(getattr(result, "rowcount", 0))
        if written:
            logger.info("plugin marked stale: %s", plugin_id)
        return written

    async def clear_stale(self, plugin_id: str) -> bool:
        """Clear stale flags on a plugins row. Idempotent; preserves is_active.

        Returns True when a write occurred (transition out of stale).
        """
        if not plugin_id:
            return False
        async with get_async_session() as session:
            result = await session.execute(
                text(
                    f"UPDATE plugins "
                    f"SET meta = COALESCE(meta, '{{}}'::jsonb) - :stale_key - :since_key "
                    f"WHERE id = :id AND (meta ? :stale_key OR meta ? :since_key)"
                ),
                {
                    "id": plugin_id,
                    "stale_key": PluginMetaKey.STALE.value,
                    "since_key": PluginMetaKey.STALE_SINCE.value,
                },
            )
            await session.commit()
        written = bool(getattr(result, "rowcount", 0))
        if written:
            logger.info("plugin cleared stale: %s", plugin_id)
        return written

    async def delete_plugin(self, plugin_id: str) -> bool:
        """Hard-delete a plugins row and cascade embeddings + permission vocab.

        Returns True when a row was deleted. Best-effort cleanup for embeddings
        and PermissionRegistry entries.
        """
        if not plugin_id:
            return False
        record = await self.get(plugin_id)
        if record is None:
            return False
        try:
            await self.delete_plugin_route_embeddings(plugin_id)
        except Exception as exc:
            logger.warning("delete_plugin: route embeddings cleanup for %s failed: %s", plugin_id, exc)
        try:
            from core.scope_management.registration import unregister_plugin_permissions
            unregister_plugin_permissions(plugin_id)
        except Exception as exc:
            logger.debug("delete_plugin: unregister permissions for %s failed: %s", plugin_id, exc)
        async with get_async_session() as session:
            result = await session.execute(
                text("DELETE FROM plugins WHERE id = :id"),
                {"id": plugin_id},
            )
            await session.commit()
            deleted = bool(getattr(result, "rowcount", 0))
        if deleted:
            logger.info("plugin deleted: %s", plugin_id)
        return deleted

    async def update_plugin_meta(self, plugin_id: str, meta: dict[str, Any]) -> None:
        """Replace the plugins.meta JSON blob for plugin_id."""
        async with get_async_session() as session:
            await session.execute(
                text("UPDATE plugins SET meta = CAST(:meta AS JSONB) WHERE id = :id"),
                {"meta": json.dumps(meta, default=str), "id": plugin_id},
            )
            await session.commit()

    async def delete_plugin_route_embeddings(self, plugin_id: str) -> int:
        """Delete route_embeddings and embedding_jobs for plugin_id. Returns embeddings deleted."""
        from db_layer.route_store import delete_plugin_route_embeddings
        return await delete_plugin_route_embeddings(plugin_id)

    async def validate_credentials_present(
        self, plugin_id: str, vault: VaultService
    ) -> list[str]:
        """Return a list of missing credential key names.

        Raises ``PluginNotFoundError`` if the plugin is not registered.
        Raises ``PluginLoadError`` if any required credentials are missing
        (with actionable ``whiskers vault set …`` instructions).
        """
        record = await self.get(plugin_id)
        if record is None:
            raise PluginNotFoundError(
                f"Plugin '{plugin_id}' is not registered in the plugins table."
            )

        required = record.required_credentials or []
        missing = await vault.missing_keys(plugin_id, required)

        if missing:
            instructions = "\n".join(
                f"  whiskers vault set {plugin_id} {k} <value>" for k in missing
            )
            raise PluginLoadError(
                f"Plugin '{plugin_id}' cannot load — missing credentials: "
                f"{', '.join(missing)}\n\nFix with:\n{instructions}"
            )

        return []  # all present


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _json_or_empty_object(value: Any) -> str:
    """Serialise a manifest object field for a NOT NULL JSONB column."""
    import json
    return json.dumps(value or {})


def _text_array_or_empty(value: Any) -> list[str]:
    """Normalise a manifest field to a Postgres TEXT[] payload."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    raise TypeError(f"Expected list[str] or None, got {type(value).__name__}")


def _credential_keys_or_empty(value: Any) -> list[str]:
    """Normalise required_credentials (string or {key, ...} object form) to TEXT[]."""
    from core.plugin_loader.credentials_loader import normalize_credential_keys

    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"Expected list or None for required_credentials, got {type(value).__name__}")
    return normalize_credential_keys(value)


def _oauth_provider_names(value: Any) -> list[str]:
    """Store only external OAuth provider names in ``external_oauth_providers``."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [str(provider) for provider in value.keys()]
    raise TypeError(
        f"Expected dict[str, Any] or None for external_oauth, got {type(value).__name__}"
    )


def _row_to_record(row) -> PluginRecord:
    meta = row[6] or {}
    # Column is SoT (core_037); fall back to legacy meta key if column null.
    content_hash = None
    try:
        content_hash = row[10]
    except (IndexError, KeyError, TypeError):
        content_hash = None
    if not content_hash and isinstance(meta, dict):
        content_hash = meta.get("content_hash") or None
    return PluginRecord(
        id=row[0],
        display_name=row[1],
        version=row[2],
        capabilities=row[3] or [],
        required_credentials=row[4] or [],
        external_oauth_providers=row[5] or [],
        meta=meta if isinstance(meta, dict) else {},
        is_active=row[7],
        registered_at=row[8],
        last_seen_at=row[9],
        content_hash=content_hash or None,
    )
