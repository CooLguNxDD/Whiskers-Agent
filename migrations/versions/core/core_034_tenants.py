"""Create tenants table and seed default tenant.

Revision ID: core_034
Revises: core_033
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa

revision = "core_034"
down_revision = "core_033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), unique=True, nullable=False),
        sa.Column("slug", sa.Text(), unique=True, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute("INSERT INTO tenants (id, name, slug) VALUES (1, 'default', 'default')")
    op.execute("ALTER SEQUENCE tenants_id_seq RESTART WITH 2;")


def downgrade() -> None:
    op.drop_table("tenants")
