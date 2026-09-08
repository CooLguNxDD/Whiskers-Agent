"""alter_server_settings_default

Revision ID: 568ee6fe3f3f (2026-06-25 19:09:43.744038)
Revises: core_029

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '568ee6fe3f3f'
down_revision: Union[str, Sequence[str], None] = 'core_029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('server_settings', 'value', server_default=sa.text("'{}'::jsonb"))


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('server_settings', 'value', server_default=sa.text("'{}'"))
