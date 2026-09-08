"""Add user tenant foreign key constraint.

Revision ID: core_035
Revises: core_034
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa

revision = "core_035"
down_revision = "core_034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill users to the default tenant if any have NULL tenant_id
    op.execute("UPDATE users SET tenant_id = 1 WHERE tenant_id IS NULL")
    
    # Add foreign key constraint users.tenant_id -> tenants.id ON DELETE RESTRICT
    op.create_foreign_key(
        "fk_users_tenant_id_tenants",
        "users",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_tenant_id_tenants", "users", type_="foreignkey")
