"""Create plugin_credentials table (Layer 2 — encrypted outbound secrets).

Revision ID: core_007
Revises: core_006
Create Date: 2026-04-14

Stores per-plugin encrypted credentials (API keys, passwords, etc.).
The value column is BYTEA encrypted with pgp_sym_encrypt using app.master_key.
Plugins read via ctx.get_credential(key_name).
"""

from alembic import op
import sqlalchemy as sa

revision = "core_007"
down_revision = "core_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_credentials",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "plugin_id",
            sa.Text(),
            sa.ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("key_name", sa.Text(), nullable=False,
                  comment="Matches required_credentials[] entry in plugin manifest"),
        sa.Column(
            "value",
            sa.LargeBinary(),
            nullable=False,
            comment="pgp_sym_encrypt(plaintext_secret, app.master_key)",
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("plugin_id", "key_name", name="uq_plugin_credentials_plugin_key"),
    )


def downgrade() -> None:
    op.drop_table("plugin_credentials")
