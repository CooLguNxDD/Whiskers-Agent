"""Revoke legacy API keys with NULL scopes.

This is a breaking cleanup. Legacy NULL-scope keys are revoked and their scopes
are updated to '[]' to resolve to deny. Clients must re-issue keys under the opt-in scope model.

Revision ID: core_032
Revises: core_031b
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa

revision = "core_032"
down_revision = "core_031b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE api_keys SET status='revoked', revoked_at=now(), scopes='[]'::jsonb WHERE scopes IS NULL;"
    )


def downgrade() -> None:
    # Revocation of legacy keys is not reversible to preserve security audit integrity.
    pass
