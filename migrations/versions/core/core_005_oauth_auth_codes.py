"""Create oauth_auth_codes table (PKCE authorization codes).

Revision ID: core_005
Revises: core_004
Create Date: 2026-04-14

Short-lived codes (10 min TTL) consumed exactly once during the PKCE flow.
used_at marks consumption; pg_cron sweeps expired rows.
"""

from alembic import op
import sqlalchemy as sa

revision = "core_005"
down_revision = "core_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_auth_codes",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.Text(), nullable=False, unique=True,
                  comment="Opaque authorization code sent to redirect_uri"),
        sa.Column(
            "client_id",
            sa.UUID(),
            sa.ForeignKey("oauth_clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scopes_granted", sa.Text(), nullable=False, server_default="",
                  comment="Space-separated scopes granted"),
        sa.Column("code_challenge", sa.Text(), nullable=False, comment="SHA-256 PKCE challenge"),
        sa.Column("code_challenge_method", sa.Text(), nullable=False, server_default="S256"),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '10 minutes'"),
        ),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True,
                  comment="NULL = unused; set on exchange to prevent replay"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_oauth_auth_codes_expires_at", "oauth_auth_codes", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_oauth_auth_codes_expires_at", table_name="oauth_auth_codes")
    op.drop_table("oauth_auth_codes")
