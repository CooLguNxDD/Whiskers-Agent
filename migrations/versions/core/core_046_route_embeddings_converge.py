"""Converge route_embeddings schema into core and retire pro_plugin local migration.

Revision ID: core_046
Revises: core_045
Create Date: 2026-07-24
"""

import os
from alembic import op
import sqlalchemy as sa

revision = "core_046"
down_revision = "core_045"
branch_labels = None
depends_on = None


def get_default_model_id() -> str:
    provider = os.environ.get("EMBED_PROVIDER", os.environ.get("LLM_PROVIDER", "openai")).lower()
    fallback_models = {
        "openai": "text-embedding-3-small",
        "gemini": "gemini-embedding-001",
        "anthropic": "text-embedding-3-small",
    }
    model_name = os.environ.get("EMBED_MODEL", "") or fallback_models.get(provider, "text-embedding-3-small")
    dims = int(os.environ.get("EMBED_DIMENSIONS", "") or 1536)
    return f"{provider}:{model_name}:{dims}"


def upgrade() -> None:
    """Idempotently converge route_embeddings schema and retire legacy pro_plugin revision."""
    op.execute(
        """
        ALTER TABLE route_embeddings
            ADD COLUMN IF NOT EXISTS doc_text TEXT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE route_embeddings
            ADD COLUMN IF NOT EXISTS search_doc tsvector
                GENERATED ALWAYS AS (to_tsvector('english', coalesce(doc_text, ''))) STORED
        """
    )
    op.execute(
        """
        ALTER TABLE route_embeddings
            ADD COLUMN IF NOT EXISTS plugin_id     TEXT,
            ADD COLUMN IF NOT EXISTS content_hash  TEXT,
            ADD COLUMN IF NOT EXISTS is_fast_path  BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS is_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS path_template TEXT,
            ADD COLUMN IF NOT EXISTS parameters    JSONB DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS model         VARCHAR,
            ADD COLUMN IF NOT EXISTS embedded_at   TIMESTAMPTZ
        """
    )

    default_model_id = get_default_model_id()
    op.get_bind().execute(
        sa.text(
            """
            UPDATE route_embeddings
            SET plugin_id     = COALESCE(plugin_id, 'plugins.pro_plugin'),
                path_template = COALESCE(path_template, path),
                content_hash  = COALESCE(content_hash, md5(coalesce(description, ''))),
                model         = COALESCE(model, :default_model_id)
            WHERE plugin_id IS NULL
               OR path_template IS NULL
               OR content_hash IS NULL
               OR model IS NULL
            """
        ),
        {"default_model_id": default_model_id},
    )

    op.execute(
        """
        ALTER TABLE route_embeddings
            ALTER COLUMN plugin_id     SET NOT NULL,
            ALTER COLUMN content_hash  SET NOT NULL,
            ALTER COLUMN path_template SET NOT NULL,
            ALTER COLUMN model         SET NOT NULL
        """
    )

    conn = op.get_bind()
    has_id = conn.execute(
        sa.text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'route_embeddings'
              AND column_name = 'id'
            """
        )
    ).fetchone()

    if not has_id:
        op.execute("ALTER TABLE route_embeddings DROP CONSTRAINT IF EXISTS route_embeddings_pkey")
        op.execute("ALTER TABLE route_embeddings ADD COLUMN id BIGSERIAL PRIMARY KEY")

    op.execute(
        """
        ALTER TABLE route_embeddings
            DROP CONSTRAINT IF EXISTS route_embeddings_plugin_op_unique,
            DROP CONSTRAINT IF EXISTS route_embeddings_pkey_op
        """
    )
    op.execute(
        """
        ALTER TABLE route_embeddings
            ADD CONSTRAINT route_embeddings_plugin_op_unique
            UNIQUE (plugin_id, operation_id, model)
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS route_embeddings_plugin_idx
            ON route_embeddings (plugin_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS route_embeddings_enabled_idx
            ON route_embeddings (plugin_id, is_enabled)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_route_embeddings_search_doc
            ON route_embeddings USING GIN (search_doc)
        """
    )

    has_table = conn.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name = 'plugin_schema_revisions'
            )
            """
        )
    ).scalar()

    if has_table:
        op.execute(
            """
            DELETE FROM plugin_schema_revisions
             WHERE plugin_id LIKE '%pro_plugin' AND revision = '0001_init'
            """
        )


def downgrade() -> None:
    """No-op downgrade.

    All schema elements converged by this migration are core-owned and established
    in earlier core revisions (core_014, core_015, core_023, core_043). Dropping
    them here would corrupt the core migration chain.
    """
    pass
