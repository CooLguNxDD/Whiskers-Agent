"""Create plugin_oauth_tokens table (Layer 2 — outbound plugin OAuth tokens).

Revision ID: core_008
Revises: core_007
Create Date: 2026-04-14

Persists access + refresh tokens for external OAuth providers used by plugins
(e.g. Google Calendar, Salesforce, Whiskers Agent backend OAuth).
Tokens are pgp_sym_encrypt'd with app.master_key.
Plugins read via ctx.get_external_token(provider).
"""

from alembic import op
import sqlalchemy as sa

revision = "core_008"
down_revision = "core_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_oauth_tokens",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "plugin_id",
            sa.Text(),
            sa.ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("provider", sa.Text(), nullable=False,
                  comment="Matches external_oauth_providers[] entry in plugin manifest"),
        sa.Column(
            "access_token",
            sa.LargeBinary(),
            nullable=False,
            comment="pgp_sym_encrypt(access_token, app.master_key)",
        ),
        sa.Column(
            "refresh_token",
            sa.LargeBinary(),
            nullable=True,
            comment="pgp_sym_encrypt(refresh_token, app.master_key) — NULL if provider does not issue refresh tokens",
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=False, server_default="",
                  comment="Space-separated scopes granted by the external provider"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("plugin_id", "provider", name="uq_plugin_oauth_tokens_plugin_provider"),
    )
    op.create_index("ix_plugin_oauth_tokens_expires_at", "plugin_oauth_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_plugin_oauth_tokens_expires_at", table_name="plugin_oauth_tokens")
    op.drop_table("plugin_oauth_tokens")
