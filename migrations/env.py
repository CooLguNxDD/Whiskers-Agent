import glob
import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool, text
from alembic import context

# ---------------------------------------------------------------------------
# Alembic Config object — access to values in alembic.ini.
# ---------------------------------------------------------------------------
config = context.config

# Set up Python logging from the ini file.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Inject DATABASE_URL from the environment so credentials are never committed.
# ---------------------------------------------------------------------------
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

# ---------------------------------------------------------------------------
# MASTER_KEY — injected into every connection as a session-level GUC so that
# pgp_sym_encrypt / pgp_sym_decrypt can use current_setting('app.master_key').
# Read lazily so CLI wrappers that load .env later still pass the final value.
# ---------------------------------------------------------------------------


def _get_master_key() -> str:
    return os.environ.get("MASTER_KEY", "").strip()

# ---------------------------------------------------------------------------
# Core-only version path discovery.
#
# Layout:
#   migrations/versions/core/  ← single core branch (core_001…core_041+)
#
# Plugin DDL lives in plugins/<pkg>/migrations/ and is applied by
# db_layer.plugin_schema_migrator.PluginSchemaMigrator on plugin load
# (not via Alembic multi-head branches).
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent  # migrations/
_versions_root = _HERE / "versions"
_core_dir = _versions_root / "core"

_version_locations: list[str] = [str(_core_dir)]

# Keep the legacy flat versions/ for backward compatibility (old 20260409_* files).
_legacy_dir = _versions_root
if _legacy_dir.is_dir():
    _version_locations.append(str(_legacy_dir))

config.set_main_option("version_locations", os.pathsep.join(_version_locations))

# ---------------------------------------------------------------------------
# target_metadata = None because all migrations use raw SQL (op.execute /
# op.create_table).  Autogenerate is intentionally disabled.
# ---------------------------------------------------------------------------
target_metadata = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inject_master_key(connection) -> None:
    """Inject MASTER_KEY as a session-level GUC on every connection."""
    key = _get_master_key()
    if key:
        # SET does not support bind parameters. Use set_config function instead.
        connection.execute(text("SELECT set_config('app.master_key', :k, false)"), {"k": key})


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (useful for review)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # Use AUTOCOMMIT so that DDL statements (CREATE TABLE, CREATE EXTENSION,
    # CREATE INDEX CONCURRENTLY, etc.) are committed immediately and are not
    # silently rolled back when wrapped in a transactional block.
    with connectable.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        # Inject MASTER_KEY before running any migration DDL so pgcrypto
        # functions work inside upgrade() / downgrade() calls.
        _inject_master_key(connection)

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
