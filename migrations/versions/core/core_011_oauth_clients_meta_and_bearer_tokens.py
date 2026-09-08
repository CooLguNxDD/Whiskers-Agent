"""Patch oauth_clients + add mcp_bearer_tokens for delegated token storage.

Revision ID: core_011
Revises: core_010
Create Date: 2026-05-05

Two additive changes:

1. oauth_clients — add ``client_data`` JSONB column.
   Stores the full ``OAuthClientInformationFull`` JSON blob so that
   ``db_load_client`` can reconstruct the Pydantic object without having to
   map every optional field individually to normalized columns.

2. mcp_bearer_tokens — new table for Layer 1 delegated bearer tokens.
   The existing ``oauth_tokens`` table (jti UUID PK) is reserved for
   self-issued JWTs signed with the keypair in ``auth_keypairs``.
   Because the current Whiskers Agent OAuth flow *delegates* token issuance to the
   Whiskers Agent backend (the MCP server passes through the backend's opaque access
   token), a separate, simpler table is needed to persist those tokens across
   restarts so that ``load_access_token`` works after a server restart.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TEXT

revision = "core_011"
down_revision = "core_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add client_data JSONB to oauth_clients ---------------------------------
    op.add_column(
        "oauth_clients",
        sa.Column(
            "client_data",
            JSONB,
            nullable=False,
            server_default="{}",
            comment="Full OAuthClientInformationFull JSON for easy reconstruction",
        ),
    )

    # 2. Create mcp_bearer_tokens ------------------------------------------------
    op.create_table(
        "mcp_bearer_tokens",
        sa.Column(
            "token",
            sa.Text(),
            primary_key=True,
            comment="Opaque bearer token string presented by the MCP client",
        ),
        sa.Column(
            "client_id",
            sa.Text(),
            nullable=False,
            comment="String client_id matching OAuthClientInformationFull.client_id",
        ),
        sa.Column(
            "scopes",
            ARRAY(TEXT),
            nullable=False,
            server_default="{}",
            comment="Scopes granted with this token",
        ),
        sa.Column(
            "expires_at",
            sa.BigInteger(),
            nullable=True,
            comment="Unix timestamp; NULL = non-expiring token",
        ),
        sa.Column(
            "issued_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_mcp_bearer_tokens_expires_at", "mcp_bearer_tokens", ["expires_at"])
    op.create_index("ix_mcp_bearer_tokens_client_id",  "mcp_bearer_tokens", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_bearer_tokens_expires_at", table_name="mcp_bearer_tokens")
    op.drop_index("ix_mcp_bearer_tokens_client_id",  table_name="mcp_bearer_tokens")
    op.drop_table("mcp_bearer_tokens")
    op.drop_column("oauth_clients", "client_data")
