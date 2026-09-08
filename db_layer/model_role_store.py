"""model_role_store — CRUD for operator DB overrides of model-role ladders + effort_map.

``server_settings['model_role_specs']`` holds ``{"roles": {role_id: raw_spec_dict},
"effort_map": {level: alias}}``. Deliberately a **separate** key from
``step_model_policy`` (see ``db_layer.step_model_settings_store``) — that
store's ``_apply_patch`` accepts and deepcopies unknown complex keys
completely unvalidated, which is exactly what ``core_graph.model_roles.role_spec``'s
fail-closed parser exists to prevent. This store is strict on write, lenient
on read.
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

MODEL_ROLE_KEY = "model_role_specs"

_EMPTY: dict[str, Any] = {"roles": {}, "effort_map": {}}


def _db_available() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


async def _read_raw() -> dict:
    """Select the raw stored value; always returns a well-shaped dict, never None."""
    async with get_async_session() as session:
        row = (await session.execute(
            select(ServerSetting.value).where(ServerSetting.key == MODEL_ROLE_KEY)
        )).fetchone()
    if row and isinstance(row[0], dict):
        stored = row[0]
        return {
            "roles": dict(stored.get("roles") or {}),
            "effort_map": dict(stored.get("effort_map") or {}),
        }
    return copy.deepcopy(_EMPTY)


async def _write_raw(value: dict) -> None:
    stmt = (
        pg_insert(ServerSetting)
        .values(key=MODEL_ROLE_KEY, value=value)
        .on_conflict_do_update(index_elements=["key"], set_={"value": value})
    )
    async with get_async_session() as session:
        await session.execute(stmt)
        await session.commit()


async def get_model_role_overrides() -> dict:
    """Raw stored overrides: ``{"roles": {role_id: raw_spec}, "effort_map": {...}}``.

    Returned dicts hold *unparsed* spec payloads (whatever was last
    successfully validated on write) — callers that need ``ModelRoleSpec``
    objects go through ``core_graph.model_roles.db_overlay.apply_db_overrides``,
    which parses leniently and skips any entry that no longer validates.
    """
    if not _db_available():
        return copy.deepcopy(_EMPTY)
    return await _read_raw()


async def set_model_role_override(role_id: str, spec: dict | None) -> dict:
    """Validate + persist (or, when ``spec`` is None, delete) one role's DB override.

    Strict on write: raises ``core_graph.model_roles.role_spec.ModelRoleSpecError``
    on an invalid spec — the caller (the config route) turns that into a 400.
    Nothing is persisted when validation fails.
    """
    from core_graph.model_roles.role_spec import parse_model_role_spec

    role_id = (role_id or "").strip()
    if not role_id:
        raise ValueError("role_id is required")

    if spec is not None:
        # Validate before touching the DB — and before touching the stored
        # dict — so a bad spec never partially persists.
        parse_model_role_spec(spec, owner="db")

    if not _db_available():
        logger.warning("set_model_role_override: DB unavailable, change not persisted")
        current = await get_model_role_overrides()
        if spec is None:
            current["roles"].pop(role_id, None)
        else:
            current["roles"][role_id] = copy.deepcopy(spec)
        return current

    current = await _read_raw()
    if spec is None:
        current["roles"].pop(role_id, None)
    else:
        current["roles"][role_id] = copy.deepcopy(spec)
    await _write_raw(current)
    return current


async def set_effort_map(effort_map: dict | None) -> dict:
    """Validate + persist (or clear) the DB-overridden effort_map.

    Strict on write: every key must be a declared effort level
    (``core_graph.model_roles.role_spec._EFFORTS``) and every value a
    declared non-``core`` alias (``core_graph.model_roles.resolver._ALIASES_NON_CORE``).
    """
    from core_graph.model_roles.resolver import _ALIASES_NON_CORE
    from core_graph.model_roles.role_spec import _EFFORTS, ModelRoleSpecError

    if effort_map is not None:
        if not isinstance(effort_map, dict):
            raise ModelRoleSpecError("effort_map must be an object")
        for level, alias in effort_map.items():
            if level not in _EFFORTS:
                raise ModelRoleSpecError(f"effort_map: unknown effort level '{level}'")
            if alias not in _ALIASES_NON_CORE and alias != "core":
                raise ModelRoleSpecError(f"effort_map: unknown alias '{alias}' for level '{level}'")

    if not _db_available():
        logger.warning("set_effort_map: DB unavailable, change not persisted")
        current = await get_model_role_overrides()
        current["effort_map"] = dict(effort_map) if effort_map else {}
        return current

    current = await _read_raw()
    current["effort_map"] = dict(effort_map) if effort_map else {}
    await _write_raw(current)
    return current


async def clear_model_role_overrides() -> None:
    """Delete the entire override row (roles + effort_map)."""
    if not _db_available():
        return
    await _write_raw(copy.deepcopy(_EMPTY))
