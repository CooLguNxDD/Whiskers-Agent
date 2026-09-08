"""Add analytics tables

Revision ID: core_024
Revises: core_chat_001
Create Date: 2026-06-12
"""
from alembic import op
import sqlalchemy as sa

revision = "core_024"
down_revision = "core_chat_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_call_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tool_name", sa.String(256), nullable=False),
        sa.Column("plugin_id", sa.String(256), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error_type", sa.String(128), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("subject", sa.String(256), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_tool_call_events_tool_name", "tool_call_events", ["tool_name"])
    op.create_index("ix_tool_call_events_created_at", "tool_call_events", ["created_at"])

    op.create_table(
        "relay_session_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("subject", sa.String(256), nullable=False),
        sa.Column("ide_id", sa.String(128), nullable=True),
        sa.Column("bytes_total", sa.BigInteger(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_relay_session_events_event", "relay_session_events", ["event"])
    op.create_index("ix_relay_session_events_created_at", "relay_session_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_relay_session_events_created_at", table_name="relay_session_events")
    op.drop_index("ix_relay_session_events_event", table_name="relay_session_events")
    op.drop_table("relay_session_events")

    op.drop_index("ix_tool_call_events_created_at", table_name="tool_call_events")
    op.drop_index("ix_tool_call_events_tool_name", table_name="tool_call_events")
    op.drop_table("tool_call_events")
