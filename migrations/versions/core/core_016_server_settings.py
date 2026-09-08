"""Create server_settings key/value table for runtime config overrides.

Revision ID: core_016
Revises: core_015
Create Date: 2026-05-22

Stores non-sensitive server configuration overrides (LLM provider, model,
RAG toggle, embedding settings).  API keys are NEVER stored here — they
remain in environment variables.
"""

from alembic import op

revision = "core_016"
down_revision = "core_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS server_settings (
            key        TEXT PRIMARY KEY,
            value      JSONB NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS server_settings")
