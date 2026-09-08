"""Create plugin_oauth_pkce_state table (transient PKCE handshake state).

Revision ID: core_009
Revises: core_008
Create Date: 2026-04-14

Short-lived rows (15-min TTL) storing the PKCE code_verifier during the
OAuth dance initiated by a plugin to acquire an external provider token.
state is the opaque value sent to the provider and returned in the callback.
code_verifier is encrypted because it is a secret.
"""

from alembic import op
import sqlalchemy as sa

revision = "core_009"
down_revision = "core_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_oauth_pkce_state",
        sa.Column("state", sa.Text(), primary_key=True,
                  comment="Opaque random string sent as OAuth state param"),
        sa.Column("plugin_id", sa.Text(), nullable=False, index=True,
                  comment="Owning plugin — NOT an FK so state survives plugin reload"),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column(
            "code_verifier",
            sa.LargeBinary(),
            nullable=False,
            comment="pgp_sym_encrypt(code_verifier, app.master_key)",
        ),
        sa.Column(
            "redirect_uri",
            sa.Text(),
            nullable=True,
            comment="Callback URL originally used for the provider authorization request",
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '15 minutes'"),
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_plugin_oauth_pkce_state_expires_at", "plugin_oauth_pkce_state", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_plugin_oauth_pkce_state_expires_at", table_name="plugin_oauth_pkce_state")
    op.drop_table("plugin_oauth_pkce_state")
