"""Nullable owning tenant on worlds for authenticated semantic v2 access.

No backfill: existing worlds stay unowned (NULL) and therefore invisible to the
tenant-scoped semantic routes/tools until an operator assigns ownership. Legacy
public spatial routes do not read or write this column.
"""
from sqlalchemy import text


def upgrade(conn) -> None:
    """Add worlds.tenant_id + lookup index; never invent ownership for existing rows."""
    conn.execute(text("ALTER TABLE worlds ADD COLUMN IF NOT EXISTS tenant_id BIGINT"))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS worlds_tenant_world_idx ON worlds (tenant_id, world_id)"
    ))
