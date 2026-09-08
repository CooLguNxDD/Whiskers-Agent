"""Plugin-local schema migrator (Phase 1 marketplace).

Discovers ordered ``NNNN_name.(sql|py)`` steps under a plugin's migrations dir,
applies pending ones against Postgres, and records them in
``plugin_schema_revisions``. Sync engine + advisory lock for multi-worker
safety; async API offloads via ``asyncio.to_thread``.

**Trust model:** Migration files under ``plugins/`` are trusted first-party code
(same surface as importing the plugin package). SQL is executed with the app DB
role; operators should apply least-privilege DB grants where possible. Python
steps can be disabled via ``PLUGIN_SCHEMA_ALLOW_PY_MIGRATIONS=0``.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from db_layer.plugin_registry_store import PluginLoadError

logger = logging.getLogger("whiskers")

# Filenames only — rejects path separators / traversal via regex gate.
_STEP_FILENAME_RE = re.compile(r"^\d{4}_[a-z0-9_]+\.(sql|py)$")

# Process-level cache: set of revision ids reachable from current alembic heads.
_CORE_ANCESTORS: set[str] | None = None
_CORE_ANCESTORS_FAILED = False
_CORE_ANCESTORS_FAILED_AT: float = 0.0
# How long a gate-computation failure stays cached before the next call retries.
# Bounds the blast radius of a transient failure (dropped connection, alembic.ini
# briefly unreadable) without hammering the DB/filesystem on every plugin load.
_CORE_ANCESTORS_FAILURE_TTL_S = 60.0

# Bounded advisory-lock wait (try-lock + poll) to avoid infinite startup hangs.
_DEFAULT_LOCK_TIMEOUT_S = 30.0
_DEFAULT_LOCK_POLL_S = 0.2
# Cap migration file reads to limit OOM from oversized SQL/seed dumps.
MAX_MIGRATION_FILE_BYTES = 10 * 1024 * 1024
_CHECKSUM_CHUNK_BYTES = 65536


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _lock_timeout_s() -> float:
    return max(0.1, _env_float("PLUGIN_SCHEMA_LOCK_TIMEOUT_S", _DEFAULT_LOCK_TIMEOUT_S))


def _lock_poll_s() -> float:
    return max(0.05, _env_float("PLUGIN_SCHEMA_LOCK_POLL_S", _DEFAULT_LOCK_POLL_S))


def _allow_py_migrations() -> bool:
    """Whether ``.py`` migration steps are permitted (default True)."""
    return _env_bool("PLUGIN_SCHEMA_ALLOW_PY_MIGRATIONS", True)


class PluginMigrationError(PluginLoadError):
    """Raised when a plugin's schema migrations cannot be applied (fail-closed)."""


@dataclass(frozen=True)
class MigrationStep:
    """One ordered migration step discovered on disk."""

    revision: str
    path: Path
    kind: Literal["sql", "py"]
    checksum: str


def _file_checksum(path: Path) -> str:
    """Return sha256 hex digest of file bytes (chunked to bound memory)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHECKSUM_CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def _assert_migration_file_size(path: Path) -> None:
    """Fail closed if a migration file exceeds ``MAX_MIGRATION_FILE_BYTES``."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PluginMigrationError(f"Cannot stat migration file {path}: {exc}") from exc
    if size > MAX_MIGRATION_FILE_BYTES:
        raise PluginMigrationError(
            f"Migration file {path.name} is {size} bytes "
            f"(limit {MAX_MIGRATION_FILE_BYTES})"
        )


def _lock_key(plugin_id: str) -> str:
    return f"plugin_schema:{plugin_id}"


class PluginSchemaMigrator:
    """Discover and apply plugin-local migration steps with a ledger + advisory lock."""

    def discover_steps(self, migrations_dir: Path) -> list[MigrationStep]:
        """List valid migration steps in lexicographic order.

        Missing or empty dirs yield ``[]``. Filenames must match
        ``NNNN_name.(sql|py)``; others are rejected (logged).
        """
        if migrations_dir is None:
            return []
        root = Path(migrations_dir)
        if not root.is_dir():
            return []

        steps: list[MigrationStep] = []
        for path in sorted(root.iterdir(), key=lambda p: p.name):
            if not path.is_file():
                continue
            name = path.name
            if not _STEP_FILENAME_RE.match(name):
                logger.warning(
                    "plugin_schema_migrator: rejecting non-conforming migration file %s",
                    path,
                )
                continue
            kind: Literal["sql", "py"] = "sql" if name.endswith(".sql") else "py"
            if kind == "py" and not _allow_py_migrations():
                logger.warning(
                    "plugin_schema_migrator: rejecting Python migration %s "
                    "(PLUGIN_SCHEMA_ALLOW_PY_MIGRATIONS disabled)",
                    path,
                )
                continue
            revision = path.stem
            try:
                # Size gate is fail-closed at apply time (pending pre-check), not
                # discover — silent omit would leave schema unrepaired with no error.
                checksum = _file_checksum(path)
            except OSError as exc:
                logger.warning(
                    "plugin_schema_migrator: cannot read %s — %s", path, exc
                )
                continue
            steps.append(
                MigrationStep(
                    revision=revision,
                    path=path,
                    kind=kind,
                    checksum=checksum,
                )
            )
        return steps

    async def migrate_plugin(
        self,
        plugin_id: str,
        plugin_version: str | None,
        migrations_dir: Path,
        *,
        min_core_revision: str | None = None,
        auto_migrate: bool = True,
    ) -> int:
        """Apply pending steps for ``plugin_id``. Returns count of newly applied steps.

        Offloads the sync engine work to a worker thread (non-blocking rule).
        Raises ``PluginMigrationError`` on gate failure, apply error, or when
        ``auto_migrate`` is False with pending steps.
        """
        return await asyncio.to_thread(
            self._migrate_sync,
            plugin_id,
            plugin_version,
            Path(migrations_dir) if migrations_dir is not None else None,
            min_core_revision,
            auto_migrate,
        )

    async def pending_steps(
        self,
        plugin_id: str,
        migrations_dir: Path,
    ) -> list[MigrationStep]:
        """Return steps on disk that are not yet in the ledger (PR4 seam)."""
        return await asyncio.to_thread(
            self._pending_steps_sync,
            plugin_id,
            Path(migrations_dir) if migrations_dir is not None else None,
        )

    def _pending_steps_sync(
        self,
        plugin_id: str,
        migrations_dir: Path | None,
    ) -> list[MigrationStep]:
        steps = self.discover_steps(migrations_dir) if migrations_dir else []
        if not steps:
            return []
        from db_layer.connection import create_db_engine

        engine = create_db_engine()
        try:
            with engine.connect() as conn:
                applied = self._read_ledger(conn, plugin_id)
        finally:
            engine.dispose()
        return [s for s in steps if s.revision not in applied]

    def _migrate_sync(
        self,
        plugin_id: str,
        plugin_version: str | None,
        migrations_dir: Path | None,
        min_core_revision: str | None,
        auto_migrate: bool,
    ) -> int:
        """Sync migrate path: gate → lock → ledger diff → per-step txn → unlock."""
        if not plugin_id:
            raise PluginMigrationError("plugin_id is required for schema migration")

        steps = self.discover_steps(migrations_dir) if migrations_dir else []
        if not steps:
            return 0

        from db_layer.connection import create_db_engine

        engine = create_db_engine()
        lock_conn = engine.connect()
        lock_key = _lock_key(plugin_id)
        applied_count = 0
        locked = False
        try:
            # Bounded try-lock so a hung peer cannot block plugin load forever.
            locked = self._acquire_advisory_lock(lock_conn, lock_key, plugin_id)
            try:
                # Session-level (not LOCAL): survives the commit after try-lock.
                lock_conn.execute(text("SET statement_timeout = '120s'"))
                lock_conn.commit()
            except SQLAlchemyError as exc:
                logger.debug(
                    "plugin_schema_migrator: could not set statement_timeout for %s: %s",
                    plugin_id,
                    exc,
                )

            # min_core_revision gate (fail closed) — after lock so only one worker checks.
            if min_core_revision:
                self._assert_min_core_revision(lock_conn, min_core_revision)

            # Re-read ledger after acquiring lock (concurrency backstop).
            applied = self._read_ledger(lock_conn, plugin_id)
            self._warn_ledger_gaps(plugin_id, steps, applied)

            pending = []
            for step in steps:
                row = applied.get(step.revision)
                if row is None:
                    pending.append(step)
                    continue
                stored_checksum = row.get("checksum") or ""
                if stored_checksum and stored_checksum != step.checksum:
                    logger.warning(
                        "plugin_schema_migrator: checksum mismatch for %s@%s "
                        "(ledger=%s disk=%s) — skipping re-run",
                        plugin_id,
                        step.revision,
                        stored_checksum[:12],
                        step.checksum[:12],
                    )

            if not pending:
                return 0

            if not auto_migrate:
                revs = ", ".join(s.revision for s in pending)
                raise PluginMigrationError(
                    f"Plugin '{plugin_id}' has pending schema migrations "
                    f"({revs}) but auto_migrate is false"
                )

            # Fail before any apply if a pending file is oversized.
            for step in pending:
                _assert_migration_file_size(step.path)

            for step in pending:
                t0 = time.perf_counter()
                try:
                    with engine.begin() as conn:
                        self._apply_step(conn, plugin_id, step)
                        ms = int((time.perf_counter() - t0) * 1000)
                        conn.execute(
                            text(
                                """
                                INSERT INTO plugin_schema_revisions
                                    (plugin_id, revision, checksum, plugin_version, execution_ms)
                                VALUES
                                    (:plugin_id, :revision, :checksum, :plugin_version, :execution_ms)
                                ON CONFLICT (plugin_id, revision) DO NOTHING
                                """
                            ),
                            {
                                "plugin_id": plugin_id,
                                "revision": step.revision,
                                "checksum": step.checksum,
                                "plugin_version": plugin_version,
                                "execution_ms": ms,
                            },
                        )
                except PluginMigrationError:
                    raise
                except Exception as exc:
                    raise PluginMigrationError(
                        f"Plugin '{plugin_id}' migration step '{step.revision}' failed: {exc}"
                    ) from exc
                applied_count += 1
                logger.info(
                    "plugin_schema_migrator: applied %s@%s (%dms)",
                    plugin_id,
                    step.revision,
                    int((time.perf_counter() - t0) * 1000),
                )

            return applied_count
        finally:
            if locked:
                try:
                    lock_conn.execute(
                        text(
                            "SELECT pg_advisory_unlock(hashtextextended(:k, 0))"
                        ),
                        {"k": lock_key},
                    )
                    lock_conn.commit()
                except (SQLAlchemyError, OSError) as unlock_exc:
                    logger.warning(
                        "plugin_schema_migrator: unlock failed for %s: %s: %s",
                        plugin_id,
                        type(unlock_exc).__name__,
                        unlock_exc,
                    )
            try:
                lock_conn.close()
            except (SQLAlchemyError, OSError) as close_exc:
                logger.debug(
                    "plugin_schema_migrator: lock_conn close failed for %s: %s",
                    plugin_id,
                    close_exc,
                )
            engine.dispose()

    def _read_ledger(self, conn, plugin_id: str) -> dict[str, dict]:
        """Return {revision: {checksum, ...}} for the plugin."""
        try:
            rows = conn.execute(
                text(
                    """
                    SELECT revision, checksum, plugin_version, applied_at, execution_ms
                    FROM plugin_schema_revisions
                    WHERE plugin_id = :plugin_id
                    """
                ),
                {"plugin_id": plugin_id},
            ).mappings().all()
        except Exception as exc:
            # Table missing (core_040 not applied) — fail closed.
            raise PluginMigrationError(
                f"Cannot read plugin_schema_revisions for '{plugin_id}' "
                f"(is core_040 applied?): {exc}"
            ) from exc
        return {r["revision"]: dict(r) for r in rows}

    def _warn_ledger_gaps(
        self,
        plugin_id: str,
        steps: list[MigrationStep],
        applied: dict[str, dict],
    ) -> None:
        """WARN when a ledgered revision is missing from disk."""
        on_disk = {s.revision for s in steps}
        for rev in applied:
            if rev not in on_disk:
                logger.warning(
                    "plugin_schema_migrator: ledgered revision %s@%s missing on disk — ignoring",
                    plugin_id,
                    rev,
                )

    def _acquire_advisory_lock(self, lock_conn, lock_key: str, plugin_id: str) -> bool:
        """Acquire session advisory lock via try-lock + backoff; raise on timeout."""
        deadline = time.monotonic() + _lock_timeout_s()
        poll = _lock_poll_s()
        while True:
            result = lock_conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"),
                {"k": lock_key},
            )
            acquired = bool(result.scalar())
            lock_conn.commit()
            if acquired:
                return True
            if time.monotonic() >= deadline:
                raise PluginMigrationError(
                    f"Plugin '{plugin_id}' schema migration lock timeout "
                    f"after {_lock_timeout_s():.1f}s "
                    f"(another worker may hold plugin_schema:{plugin_id})"
                )
            time.sleep(poll)

    def _apply_step(self, conn, plugin_id: str, step: MigrationStep) -> None:
        """Execute one step inside an open transaction (caller owns commit)."""
        _assert_migration_file_size(step.path)
        if step.kind == "sql":
            sql = step.path.read_text(encoding="utf-8")
            if not sql.strip():
                return
            # Whole file as one driver batch (no CREATE INDEX CONCURRENTLY).
            conn.exec_driver_sql(sql)
            return

        if not _allow_py_migrations():
            raise PluginMigrationError(
                f"Python migration {step.path.name} blocked "
                f"(PLUGIN_SCHEMA_ALLOW_PY_MIGRATIONS disabled)"
            )

        # Python step: must expose upgrade(conn). Trusted first-party code only.
        # Namespace by plugin_id so concurrent plugins with 0001_init do not collide.
        safe_id = plugin_id.replace(".", "_").replace("-", "_")
        mod_name = f"plugin_mig_{safe_id}_{step.revision}"
        spec = importlib.util.spec_from_file_location(mod_name, step.path)
        if spec is None or spec.loader is None:
            raise PluginMigrationError(
                f"Cannot load migration module {step.path}"
            )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        upgrade = getattr(module, "upgrade", None)
        if not callable(upgrade):
            raise PluginMigrationError(
                f"Migration {step.path} has no callable upgrade(conn)"
            )
        upgrade(conn)

    def _assert_min_core_revision(self, conn, min_core_revision: str) -> None:
        """Fail closed unless ``min_core_revision`` is an ancestor of a current head."""
        ancestors = self._get_core_ancestors(conn)
        if min_core_revision not in ancestors:
            raise PluginMigrationError(
                f"min_core_revision '{min_core_revision}' is not satisfied by "
                f"current alembic heads (ancestor set size={len(ancestors)})"
            )

    def _get_core_ancestors(self, conn) -> set[str]:
        """Return revision ids reachable from alembic_version heads (cached).

        A prior failure stays cached (fail-closed) only for
        ``_CORE_ANCESTORS_FAILURE_TTL_S`` — after that the next call retries,
        so a transient failure (dropped connection, alembic.ini briefly
        unreadable) doesn't permanently poison the gate for the process
        lifetime.
        """
        global _CORE_ANCESTORS, _CORE_ANCESTORS_FAILED, _CORE_ANCESTORS_FAILED_AT
        if _CORE_ANCESTORS is not None:
            return _CORE_ANCESTORS
        if _CORE_ANCESTORS_FAILED:
            if time.monotonic() - _CORE_ANCESTORS_FAILED_AT < _CORE_ANCESTORS_FAILURE_TTL_S:
                raise PluginMigrationError(
                    "min_core_revision gate unavailable (previous load failure)"
                )
            # TTL expired — clear and retry below.
            _CORE_ANCESTORS_FAILED = False

        try:
            heads = [
                row[0]
                for row in conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchall()
            ]
            if not heads:
                raise PluginMigrationError(
                    "alembic_version is empty — cannot evaluate min_core_revision"
                )

            from alembic.config import Config
            from alembic.script import ScriptDirectory

            # Prefer project-root alembic.ini (cwd may vary under docker/tests).
            ini_candidates = [
                Path("alembic.ini"),
                Path(__file__).resolve().parent.parent / "alembic.ini",
            ]
            ini_path = next((p for p in ini_candidates if p.is_file()), None)
            if ini_path is None:
                raise PluginMigrationError(
                    "alembic.ini not found — cannot evaluate min_core_revision"
                )

            cfg = Config(str(ini_path))
            # Ensure version_locations points at core (plugins branches may be gone).
            core_dir = ini_path.parent / "migrations" / "versions" / "core"
            if core_dir.is_dir():
                cfg.set_main_option("version_locations", str(core_dir))

            script = ScriptDirectory.from_config(cfg)
            ancestors: set[str] = set()
            for head in heads:
                try:
                    for rev in script.iterate_revisions(head, "base"):
                        ancestors.add(rev.revision)
                except Exception:
                    # Head may be a retired plugin branch still listed until core_041 —
                    # try including the bare head id if known to the script map.
                    try:
                        if script.get_revision(head) is not None:
                            ancestors.add(head)
                    except Exception:
                        logger.debug(
                            "plugin_schema_migrator: unknown alembic head %s", head
                        )
                    continue
            # Always include head ids themselves.
            ancestors.update(heads)
            _CORE_ANCESTORS = ancestors
            return ancestors
        except PluginMigrationError:
            _CORE_ANCESTORS_FAILED = True
            _CORE_ANCESTORS_FAILED_AT = time.monotonic()
            raise
        except Exception as exc:
            _CORE_ANCESTORS_FAILED = True
            _CORE_ANCESTORS_FAILED_AT = time.monotonic()
            raise PluginMigrationError(
                f"min_core_revision gate failed closed: {exc}"
            ) from exc


def reset_core_ancestor_cache() -> None:
    """Test helper: clear process-level min_core_revision cache."""
    global _CORE_ANCESTORS, _CORE_ANCESTORS_FAILED, _CORE_ANCESTORS_FAILED_AT
    _CORE_ANCESTORS = None
    _CORE_ANCESTORS_FAILED = False
    _CORE_ANCESTORS_FAILED_AT = 0.0
