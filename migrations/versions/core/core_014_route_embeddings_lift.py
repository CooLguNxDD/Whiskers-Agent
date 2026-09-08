"""Lift route_embeddings ownership to core; add embedding_jobs queue.

Revision ID: core_014
Revises: core_013
Create Date: 2026-05-21

Promotes route_embeddings from pro_plugin (pro_001) to a core-owned table so
the dynamic graph orchestrator can be reached from any plugin (including
free-tier installs that never load pro_plugin).

Idempotent:
  * If pro_001 already created the table with the old schema, this ALTERs
    in place and backfills plugin_id='plugins.pro_plugin' for existing rows.
  * If pro_plugin is absent, CREATE TABLE IF NOT EXISTS builds it fresh.

Also creates the embedding_jobs queue consumed by the async embedding worker
(FOR UPDATE SKIP LOCKED claim pattern, LISTEN embedding_jobs_new wake-up).
"""

from alembic import op

revision = "core_014"
down_revision = "core_013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import os
    dim = os.environ.get("EMBED_DIMENSIONS", "1536")

    # ------------------------------------------------------------------
    # route_embeddings — converge schema regardless of whether pro_001 ran.
    # CREATE-IF-NOT-EXISTS with the minimal legacy column set so the
    # subsequent ALTERs apply identically on both code paths.
    # ------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS route_embeddings (
            operation_id  VARCHAR PRIMARY KEY,
            path          TEXT NOT NULL,
            method        VARCHAR(10) NOT NULL,
            description   TEXT NOT NULL,
            meta          JSONB DEFAULT '{{}}',
            embedding     VECTOR({dim}),
            synced_at     TIMESTAMPTZ DEFAULT now()
        )
    """)

    # Additive new columns (idempotent)
    op.execute("""
        ALTER TABLE route_embeddings
            ADD COLUMN IF NOT EXISTS plugin_id     TEXT,
            ADD COLUMN IF NOT EXISTS content_hash  TEXT,
            ADD COLUMN IF NOT EXISTS is_fast_path  BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS path_template TEXT,
            ADD COLUMN IF NOT EXISTS parameters    JSONB DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS embedded_at   TIMESTAMPTZ
    """)

    # Backfill legacy rows so the new NOT NULL columns can be enforced.
    op.execute("""
        UPDATE route_embeddings
        SET plugin_id     = COALESCE(plugin_id, 'plugins.pro_plugin'),
            path_template = COALESCE(path_template, path),
            content_hash  = COALESCE(content_hash, md5(coalesce(description, '')))
        WHERE plugin_id IS NULL
           OR path_template IS NULL
           OR content_hash IS NULL
    """)

    op.execute("""
        ALTER TABLE route_embeddings
            ALTER COLUMN plugin_id     SET NOT NULL,
            ALTER COLUMN content_hash  SET NOT NULL,
            ALTER COLUMN path_template SET NOT NULL
    """)

    # Replace operation_id PK with surrogate id + UNIQUE(plugin_id, operation_id).
    # Same operation_id can now exist under multiple plugins.
    op.execute("ALTER TABLE route_embeddings DROP CONSTRAINT IF EXISTS route_embeddings_pkey")
    op.execute("ALTER TABLE route_embeddings ADD COLUMN IF NOT EXISTS id BIGSERIAL PRIMARY KEY")
    op.execute("""
        ALTER TABLE route_embeddings
            DROP CONSTRAINT IF EXISTS route_embeddings_plugin_op_unique
    """)
    op.execute("""
        ALTER TABLE route_embeddings
            ADD CONSTRAINT route_embeddings_plugin_op_unique
            UNIQUE (plugin_id, operation_id)
    """)

    # HNSW index for cosine top-k search (embedder_node).
    op.execute("""
        CREATE INDEX IF NOT EXISTS route_embeddings_hnsw
            ON route_embeddings USING hnsw (embedding vector_cosine_ops)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS route_embeddings_plugin_idx
            ON route_embeddings (plugin_id)
    """)

    # ------------------------------------------------------------------
    # embedding_jobs — Postgres-native work queue for the async embedding
    # worker. Producer NOTIFY embedding_jobs_new wakes the worker; worker
    # claims batches via FOR UPDATE SKIP LOCKED.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS embedding_jobs (
            id            BIGSERIAL PRIMARY KEY,
            plugin_id     TEXT NOT NULL,
            operation_id  TEXT NOT NULL,
            content_hash  TEXT NOT NULL,
            payload       JSONB NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',
            attempts      INTEGER NOT NULL DEFAULT 0,
            last_error    TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            claimed_at    TIMESTAMPTZ,
            completed_at  TIMESTAMPTZ,
            UNIQUE (plugin_id, operation_id, content_hash)
        )
    """)

    # Partial index — the SKIP LOCKED scan only touches pending rows.
    op.execute("""
        CREATE INDEX IF NOT EXISTS embedding_jobs_pending
            ON embedding_jobs (created_at)
            WHERE status = 'pending'
    """)


def downgrade() -> None:
    import sqlalchemy as sa
    # Embedding queue can be dropped wholesale — it's transient state.
    op.execute("DROP INDEX IF EXISTS embedding_jobs_pending")
    op.execute("DROP TABLE IF EXISTS embedding_jobs")

    # Check if route_embeddings table exists before modifying it
    conn = op.get_bind()
    table_exists = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'route_embeddings')"
    )).scalar()

    if table_exists:
        # route_embeddings reverts to pro_001 shape. Drop new indexes + constraints
        # + columns, then restore operation_id PK.
        op.execute("DROP INDEX IF EXISTS route_embeddings_hnsw")
        op.execute("DROP INDEX IF EXISTS route_embeddings_plugin_idx")
        op.execute("ALTER TABLE route_embeddings DROP CONSTRAINT IF EXISTS route_embeddings_plugin_op_unique")
        op.execute("ALTER TABLE route_embeddings DROP CONSTRAINT IF EXISTS route_embeddings_pkey")
        op.execute("ALTER TABLE route_embeddings ADD PRIMARY KEY (operation_id)")
        op.execute("""
            ALTER TABLE route_embeddings
                DROP COLUMN IF EXISTS id,
                DROP COLUMN IF EXISTS plugin_id,
                DROP COLUMN IF EXISTS content_hash,
                DROP COLUMN IF EXISTS is_fast_path,
                DROP COLUMN IF EXISTS path_template,
                DROP COLUMN IF EXISTS parameters,
                DROP COLUMN IF EXISTS embedded_at
        """)
