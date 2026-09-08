"""
permission_store — CRUD for per-(plugin,operation) read/write policy.

Sparse table: absent row means default policy (allow_read=True, allow_write=True,
require_confirmation=True). Used by the permission_gate node to decide whether
a step needs confirmation or is denied.
"""

from __future__ import annotations

import logging
from typing import Any

from utils.plugin_ids import canonical_plugin_id, strip_operation_prefix

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db_layer.connection import get_async_session
from db_layer.models import ToolPermission

logger = logging.getLogger("whiskers")


DEFAULT_POLICY = {
    "allow_read": True,
    "allow_write": True,
    "require_confirmation": True,
}


async def get_permission(plugin_id: str, operation_id: str) -> dict[str, Any]:
    """Return policy for (plugin, op), or DEFAULT if absent.
    Accepts either qualified (plugin__op) or raw op name; strips prefix for lookup.
    """
    if not plugin_id or not operation_id:
        return dict(DEFAULT_POLICY)
    plugin_id = canonical_plugin_id(plugin_id)
    op = strip_operation_prefix(plugin_id, operation_id)
    async with get_async_session() as session:
        row = await session.execute(
            select(
                ToolPermission.allow_read,
                ToolPermission.allow_write,
                ToolPermission.require_confirmation,
            ).where(
                ToolPermission.plugin_id == plugin_id,
                ToolPermission.operation_id == op,
            )
        )
        rec = row.fetchone()
    if rec is None:
        return dict(DEFAULT_POLICY)
    return {
        "allow_read": bool(rec[0]),
        "allow_write": bool(rec[1]),
        "require_confirmation": bool(rec[2]),
    }


async def set_permission(
    plugin_id: str,
    operation_id: str,
    *,
    allow_read: bool | None = None,
    allow_write: bool | None = None,
    require_confirmation: bool | None = None,
) -> dict[str, Any]:
    """Upsert policy row for (plugin, op). Only provided fields are touched."""
    if not plugin_id or not operation_id:
        raise ValueError("plugin_id and operation_id required")
    plugin_id = canonical_plugin_id(plugin_id)
    op = strip_operation_prefix(plugin_id, operation_id)

    # fetch current or default
    current = await get_permission(plugin_id, op)
    new_policy = {
        "allow_read": current["allow_read"] if allow_read is None else bool(allow_read),
        "allow_write": current["allow_write"] if allow_write is None else bool(allow_write),
        "require_confirmation": current["require_confirmation"] if require_confirmation is None else bool(require_confirmation),
    }

    stmt = (
        pg_insert(ToolPermission)
        .values(
            plugin_id=plugin_id,
            operation_id=op,
            allow_read=new_policy["allow_read"],
            allow_write=new_policy["allow_write"],
            require_confirmation=new_policy["require_confirmation"],
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=["plugin_id", "operation_id"],
            set_={
                "allow_read": new_policy["allow_read"],
                "allow_write": new_policy["allow_write"],
                "require_confirmation": new_policy["require_confirmation"],
                "updated_at": func.now(),
            },
        )
    )
    async with get_async_session() as session:
        await session.execute(stmt)
        await session.commit()

    logger.info(
        "tool_permission: %s.%s -> read=%s write=%s confirm=%s",
        plugin_id, op,
        new_policy["allow_read"], new_policy["allow_write"], new_policy["require_confirmation"],
    )
    return new_policy


async def get_plugin_permissions(plugin_id: str) -> dict[str, dict[str, Any]]:
    """Return {operation_id: policy_dict} for all rows of a plugin."""
    plugin_id = canonical_plugin_id(plugin_id)
    async with get_async_session() as session:
        rows = await session.execute(
            select(
                ToolPermission.operation_id,
                ToolPermission.allow_read,
                ToolPermission.allow_write,
                ToolPermission.require_confirmation,
            ).where(ToolPermission.plugin_id == plugin_id)
        )
        result = {}
        for op, ar, aw, rc in rows.all():
            result[op] = {
                "allow_read": bool(ar),
                "allow_write": bool(aw),
                "require_confirmation": bool(rc),
            }
    return result
