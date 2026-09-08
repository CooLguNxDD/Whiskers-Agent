"""Add api_keys table for long-lived API keys.

Revision ID: core_029
Revises: core_028
Create Date: 2026-06-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "core_029"
down_revision = "core_028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("value", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS api_keys_token_hash_idx ON api_keys (token_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS api_keys_subject_idx ON api_keys (subject)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS api_keys_key_id_idx ON api_keys (key_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS api_keys_key_id_idx")
    op.execute("DROP INDEX IF EXISTS api_keys_subject_idx")
    op.execute("DROP INDEX IF EXISTS api_keys_token_hash_idx")
    op.drop_table("api_keys")
