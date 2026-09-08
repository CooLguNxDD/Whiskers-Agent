"""plugin_gate_store — CRUD for operator DB overrides of plugin level-3 gates.

``server_settings['plugin_gate_specs']`` holds ``{plugin_id: raw_gate_dict}``.
A DB override **fully replaces** the plugin's manifest-declared gate (never
merges) and may widen it — see ``core.scope_management.gate_overlay``.
Strict on write (``core.scope_management.gates.GateSpecError`` -> the config
route turns that into a 400), lenient on read (a stored entry that no longer
validates is skipped + logged, never crashes the overlay). Structure copied
from ``db_layer/model_role_store.py`` — keep the two in sync if that
template changes.
"""

from __future__ import annotations

import copy
import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db_layer.connection import get_async_session
from db_layer.models import ServerSetting

logger = logging.getLogger("whiskers")

PLUGIN_GATE_KEY = "plugin_gate_specs"

_EMPTY: dict[str, Any] = {}


def _db_available() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


async def _read_raw() -> dict:
    """Select the raw stored value; always returns a dict, never None."""
    async with get_async_session() as session:
        row = (await session.execute(
            select(ServerSetting.value).where(ServerSetting.key == PLUGIN_GATE_KEY)
        )).fetchone()
    if row and isinstance(row[0], dict):
        return dict(row[0])
    return copy.deepcopy(_EMPTY)


async def _write_raw(value: dict) -> None:
    stmt = (
        pg_insert(ServerSetting)
        .values(key=PLUGIN_GATE_KEY, value=value)
        .on_conflict_do_update(index_elements=["key"], set_={"value": value})
    )
    async with get_async_session() as session:
        await session.execute(stmt)
        await session.commit()


async def get_plugin_gate_overrides() -> dict:
    """Raw stored overrides: ``{plugin_id: raw_gate_dict}``.

    Returned dict holds *unparsed* spec payloads — callers that need
    ``PluginGateSpec`` objects go through
    ``core.scope_management.gate_overlay.apply_db_overrides``, which parses
    leniently and skips any entry that no longer validates.
    """
    if not _db_available():
        return copy.deepcopy(_EMPTY)
    return await _read_raw()


async def set_plugin_gate_override(plugin_id: str, spec: dict | None) -> dict:
    """Validate + persist (or, when ``spec`` is None, delete) one plugin's DB override.

    Strict on write: raises ``core.scope_management.gates.GateSpecError`` on
    an invalid spec — the caller (the config route) turns that into a 400.
    Nothing is persisted when validation fails.
    """
    from core.scope_management.gates import parse_gate_spec

    plugin_id = (plugin_id or "").strip()
    if not plugin_id:
        raise ValueError("plugin_id is required")

    if spec is not None:
        parse_gate_spec(plugin_id, spec, owner="db")

    if not _db_available():
        logger.warning("set_plugin_gate_override: DB unavailable, change not persisted")
        current = await get_plugin_gate_overrides()
        if spec is None:
            current.pop(plugin_id, None)
        else:
            current[plugin_id] = copy.deepcopy(spec)
        return current

    current = await _read_raw()
    if spec is None:
        current.pop(plugin_id, None)
    else:
        current[plugin_id] = copy.deepcopy(spec)
    await _write_raw(current)
    return current


async def clear_plugin_gate_overrides() -> None:
    """Delete the entire override row."""
    if not _db_available():
        return
    await _write_raw(copy.deepcopy(_EMPTY))
