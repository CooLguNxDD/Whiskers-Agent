"""Create auth_keypairs table for RS256 JWT signing.

Revision ID: core_003
Revises: core_002
Create Date: 2026-04-14

Private keys are stored pgp_sym_encrypt'd with app.master_key.
Only one keypair is active at a time (key rotation via is_active flag).
"""

from alembic import op
import sqlalchemy as sa

revision = "core_003"
down_revision = "core_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_keypairs",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "private_key",
            sa.LargeBinary(),
            nullable=False,
            comment="pgp_sym_encrypt(PEM private key, app.master_key)",
        ),
        sa.Column("public_key", sa.Text(), nullable=False, comment="PEM public key (plaintext — safe to store)"),
        sa.Column("kid", sa.Text(), nullable=False, unique=True, comment="Key ID — embedded in JWT header"),
        sa.Column("algorithm", sa.Text(), nullable=False, server_default="RS256"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("rotated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Partial index: enforce at most one active keypair per algorithm
    op.create_index(
        "uq_auth_keypairs_active_algorithm",
        "auth_keypairs",
        ["algorithm"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index("uq_auth_keypairs_active_algorithm", table_name="auth_keypairs")
    op.drop_table("auth_keypairs")
