"""Create llm_pool table for the dynamic LLM provider pool.

A pool of selectable chat + embedding models. Each entry's API token is stored
in the ``api_key`` BYTEA column encrypted with pgcrypto using the same
``app.master_key`` GUC as the vault (db_layer/vault.py). The active chat /
embedding selection lives in ``server_settings`` under key ``llm_active``.

Revision ID: core_019
Revises: core_018
Create Date: 2026-05-29
"""

from alembic import op

revision = "core_019"
down_revision = "core_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS llm_pool (
            id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            name        TEXT         NOT NULL UNIQUE,
            kind        TEXT         NOT NULL DEFAULT 'chat',
            provider    TEXT         NOT NULL,
            model       TEXT         NOT NULL,
            dimensions  INTEGER,
            base_url    TEXT,
            api_key     BYTEA,
            is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS llm_pool_kind_idx ON llm_pool (kind)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS llm_pool_kind_idx")
    op.execute("DROP TABLE IF EXISTS llm_pool")
