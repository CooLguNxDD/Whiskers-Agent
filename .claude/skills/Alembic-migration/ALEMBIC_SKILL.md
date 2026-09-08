---
name: alembic
description: Alembic database migration cheatsheet — use for Alembic migrations, schema versioning, upgrade/downgrade scripts, autogenerate, env.py setup, async SQLAlchemy, branching, stamping, or managing schema changes with Alembic.
---

# Alembic Migration Cheatsheet

---

## 1. Setup

```bash
pip install alembic sqlalchemy
alembic init migrations
```

**alembic.ini essentials:**
```ini
[alembic]
script_location = migrations
sqlalchemy.url = postgresql+psycopg2://user:pass@localhost/mydb
```
> Inject URL from env in `env.py` — don't commit credentials.

---

## 2. env.py Patterns

### Sync
```python
import os
from sqlalchemy import engine_from_config, pool
from alembic import context
from myapp.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
target_metadata = Base.metadata

def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
```

### Async (asyncpg — project standard)
```python
import asyncio
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool
from alembic import context

def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations():
    connectable = async_engine_from_config(config.get_section(config.config_ini_section),
                                           prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as conn:
        await conn.run_sync(do_run_migrations)
    await connectable.dispose()

def run_migrations_online():
    asyncio.run(run_async_migrations())
```

---

## 3. CLI Quick Reference

| Command | What it does |
|---|---|
| `alembic revision -m "msg"` | New empty revision |
| `alembic revision --autogenerate -m "msg"` | New revision from model diff |
| `alembic upgrade head` | Apply all pending |
| `alembic upgrade +2` | Apply next 2 |
| `alembic downgrade -1` | Roll back 1 |
| `alembic downgrade base` | Roll back to beginning |
| `alembic current` | Show current DB revision |
| `alembic history --verbose` | Full revision DAG |
| `alembic heads` | Show un-merged branch tips |
| `alembic merge -m "msg" rev1 rev2` | Merge two branches |
| `alembic stamp head` | Mark DB up-to-date (no SQL run) |
| `alembic stamp <rev>` | Force DB to specific revision |
| `alembic check` | Check for un-generated autogenerate changes |

---

## 4. Revision Script Anatomy

```python
"""add user email — Revision ID: a1b2c3 — Revises: 9f8e7d"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "9f8e7d6c5b4a"  # None = first migration
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.create_index("ix_users_email", "users", ["email"], unique=True)

def downgrade():
    op.drop_index("ix_users_email", table_name="users")
    op.drop_column("users", "email")
```

---

## 5. `op` Operations Reference

```python
# Table ops
op.create_table("widgets",
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(100), nullable=False),
)
op.drop_table("widgets")
op.rename_table("widgets", "gadgets")

# Column ops
op.add_column("t", sa.Column("col", sa.String(50), nullable=True))
op.drop_column("t", "col")
op.alter_column("t", "col",
    new_column_name="new_col", type_=sa.Text(), nullable=False,
    existing_type=sa.String(50), existing_nullable=True)

# Indexes & constraints
op.create_index("ix_t_col", "t", ["col1", "col2"])
op.create_index("ix_partial", "t", ["col"], postgresql_where=sa.text("active = true"))
op.drop_index("ix_t_col", table_name="t")
op.create_unique_constraint("uq_t_col", "t", ["col"])
op.create_foreign_key("fk_t_other", "t", "other", ["other_id"], ["id"], ondelete="CASCADE")
op.drop_constraint("fk_t_other", "t", type_="foreignkey")

# Raw SQL
op.execute(sa.text("UPDATE users SET role = :role WHERE id = :id"), {"role": "admin", "id": 1})
```

---

## 6. Autogenerate

Requires `target_metadata = Base.metadata` in `env.py` + all models imported before autogenerate runs.

**Detects:** table add/drop, column add/drop, type changes (`compare_type=True`), nullable, indexes, unique constraints

**Misses (write manually):** renames, stored procedures/views/triggers, data migrations, partial index conditions, custom types

```python
context.configure(connection=conn, target_metadata=target_metadata,
                  compare_type=True, compare_server_default=True)
```

---

## 7. Data Migrations

```python
from sqlalchemy.sql import table, column

def upgrade():
    users = table("users",  # lightweight ref — never import ORM models
        column("id", sa.Integer),
        column("full_name", sa.String),
        column("first_name", sa.String),
        column("last_name", sa.String),
    )
    op.add_column("users", sa.Column("full_name", sa.String(200)))
    op.get_bind().execute(users.update().values(
        full_name=users.c.first_name + " " + users.c.last_name
    ))
```

---

## 8. Branching & Merge

```
base → A → B → D (main)
                 ↘ C (feature branch)
```

```bash
alembic merge -m "merge feature into main" D C  # creates M with down_revision=(D,C)
alembic upgrade head
```

> **Whiskers Agent note:** Plugin multi-head Alembic branches under `migrations/versions/plugins/`
> were **retired** (`core_041`). Host Alembic is **core-only** (`migrations/versions/core/`).
> Plugin schema travels with each pack — see §13.

---

## 9. Programmatic Usage

```python
from alembic.config import Config
from alembic import command

cfg = Config("alembic.ini")
command.upgrade(cfg, "head")
command.downgrade(cfg, "-1")
command.revision(cfg, message="auto", autogenerate=True)
command.stamp(cfg, "head")
```

---

## 10. Enum Handling (PostgreSQL)

```python
my_enum = sa.Enum("active", "inactive", name="status_enum")
my_enum.create(op.get_bind(), checkfirst=True)
op.add_column("users", sa.Column("status", my_enum))

# Drop: column first, then enum
op.drop_column("users", "status")
my_enum.drop(op.get_bind(), checkfirst=True)

# Add value (PG 10+, cannot be inside transaction)
op.execute("ALTER TYPE status_enum ADD VALUE 'pending'")
```

---

## 11. Common Pitfalls

| Problem | Fix |
|---|---|
| `Can't locate revision` | `alembic stamp head` if DB pre-exists schema |
| Multiple heads | `alembic merge heads` |
| Autogenerate sees no changes | Ensure models imported in `env.py` |
| Enum drop fails | Drop column first, then `my_enum.drop()` |
| FK constraint error on downgrade | Drop FK constraints before dropping tables |
| Async driver with sync engine | Use `async_engine_from_config` + `run_sync` |

---

## 12. Project-Specific Scripts

The project provides wrapper scripts to simplify database operations and handle async execution.

| Script | Purpose |
|---|---|
| `python scripts/migrate.py` | Upgrade to `head` by default. |

---

## 13. Plugin-local schema migrations (not Alembic)

Marketplace Phase 1: plugin DDL ships inside the plugin package and auto-applies on load.

| Piece | Location |
|---|---|
| Step files | `plugins/<pkg>/migrations/NNNN_name.(sql\|py)` (lex order; revision = stem) |
| Manifest | optional `"schema": { "migrations_path", "min_core_revision", "auto_migrate" }` |
| Runner | `db_layer/plugin_schema_migrator.py` → `PluginSchemaMigrator` |
| Ledger | `plugin_schema_revisions` (`core_040`) |
| Hook | `core/plugin_loader/plugin_loader.py` `_run_plugin_migrations` in `_load_one_spec` (fail-closed → `meta.migration_error`) |
| Parse | `core/plugin_loader/resolver.py` `parse_schema_cfg` (sandboxed path under `plugins/`) |

Rules:
- **Idempotent DDL** (`IF NOT EXISTS` / guarded ALTERs). No `CREATE INDEX CONCURRENTLY` (per-step txn).
- `.sql` — whole file via `exec_driver_sql`. `.py` — `def upgrade(conn)` on a sync SQLAlchemy `Connection`.
- Sync engine (`create_db_engine`) + `asyncio.to_thread`; MASTER_KEY GUC already on connect.
- Multi-worker: `pg_advisory_lock(hashtextextended('plugin_schema:'||plugin_id, 0))`.
- `min_core_revision`: Alembic `ScriptDirectory` **ancestor set** membership (never lexicographic).
- Operator still runs `python scripts/migrate.py` for **core** only; plugin steps re-run as no-ops and ledger.
| `python scripts/migrate.py upgrade <rev>` | Upgrade to a specific revision. |
| `python scripts/migrate.py downgrade -1` | Downgrade by one step. |
| `python scripts/migrate.py downgrade base` | Wipe the schema. |
| `python scripts/migrate.py current` | Show the current revision. |
| `python scripts/migrate.py history` | Show the migration history. |
| `python scripts/reseed.py` | `TRUNCATE` tables (keeps schema, needs `psql` on PATH). |
| `python scripts/reseed.py --full` | `downgrade base` → `upgrade head` (full reset). |

---

## 13. Testing Migrations

```python
@pytest.fixture(scope="session")
def migrated_db():
    engine = create_engine("postgresql://localhost/test_db")
    cfg = Config("alembic.ini")
    cfg.attributes["connection"] = engine.connect()
    command.upgrade(cfg, "head")
    yield engine
    command.downgrade(cfg, "base")
    engine.dispose()
```

---

## Dropped
- Setup: cut `--template async/pyproject` variants (use standard `init`)
- alembic.ini: cut prose note about credentials
- env.py: cut inline comments
- CLI table: cut "Revision symbols" section
- Revision anatomy: cut boilerplate header block
- op operations: cut SQLite batch_alter_table (irrelevant for PG project)
- Branching: cut multi-db / named-branch section
- pyproject.toml config section: not used in project
- Post-write hooks section: not used
- Testing: cut verbose test count explanation
