"""Create oauth_tokens table (access + refresh JWTs).

Revision ID: core_006
Revises: core_005
Create Date: 2026-04-14

Replaces the old oauth_access_tokens blob table.
jti (JWT ID) is the primary key — matched against the jti claim in the bearer token.
revoked_at = NULL means the token is valid; setting it revokes the token without deletion.
"""

from alembic import op
import sqlalchemy as sa

revision = "core_006"
down_revision = "core_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_tokens",
        sa.Column("jti", sa.UUID(), primary_key=True, comment="JWT ID claim — matches jti in bearer token"),
        sa.Column(
            "client_id",
            sa.UUID(),
            sa.ForeignKey("oauth_clients.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "token_type",
            sa.Text(),
            nullable=False,
            comment="'access' or 'refresh'",
        ),
        sa.Column("scopes", sa.Text(), nullable=False, server_default="",
                  comment="Space-separated scopes"),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("issued_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True,
                  comment="NULL = valid; non-NULL = revoked"),
        sa.CheckConstraint("token_type IN ('access', 'refresh')", name="ck_oauth_tokens_type"),
    )
    op.create_index("ix_oauth_tokens_expires_at", "oauth_tokens", ["expires_at"])
    op.create_index("ix_oauth_tokens_revoked_at", "oauth_tokens", ["revoked_at"])


def downgrade() -> None:
    op.drop_index("ix_oauth_tokens_revoked_at", table_name="oauth_tokens")
    op.drop_index("ix_oauth_tokens_expires_at", table_name="oauth_tokens")
    op.drop_table("oauth_tokens")
