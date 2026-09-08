"""Create oauth_clients table (Layer 1 — Inbound OAuth).

Revision ID: core_004
Revises: core_003
Create Date: 2026-04-14

Replaces the old json-blob oauth_clients table with a normalised schema.
client_secret is nullable (NULL = public PKCE client, no secret required).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

revision = "core_004"
down_revision = "core_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop legacy table if it exists (created by old flat migration)
    op.execute("DROP TABLE IF EXISTS oauth_clients CASCADE")
    op.execute("DROP TABLE IF EXISTS oauth_access_tokens CASCADE")

    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("client_id", sa.Text(), nullable=False, unique=True,
                  comment="Public client identifier sent in Authorization header"),
        sa.Column(
            "client_secret",
            sa.LargeBinary(),
            nullable=True,
            comment="pgp_sym_encrypt(secret, app.master_key) — NULL for public clients",
        ),
        sa.Column("grant_types", ARRAY(TEXT), nullable=False, server_default="{}",
                  comment="e.g. [authorization_code, refresh_token]"),
        sa.Column("redirect_uris", ARRAY(TEXT), nullable=False, server_default="{}"),
        sa.Column("scopes", ARRAY(TEXT), nullable=False, server_default="{}"),
        sa.Column("client_name", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("oauth_clients")
