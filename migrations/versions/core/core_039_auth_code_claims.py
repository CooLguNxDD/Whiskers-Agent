"""Add extra_claims JSONB to oauth_auth_codes for restart-safe role/tenant.

Revision ID: core_039
Revises: core_038
Create Date: 2026-07-13

Persists ocat_role / ocat_tenant / ocat_user_id alongside the PKCE auth code
so JWT mint survives a process restart between authorize and token exchange.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "core_039"
down_revision = "core_038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oauth_auth_codes",
        sa.Column(
            "extra_claims",
            JSONB(),
            nullable=True,
            comment="ocat_role/ocat_tenant/ocat_user_id for JWT mint after restart",
        ),
    )


def downgrade() -> None:
    op.drop_column("oauth_auth_codes", "extra_claims")
