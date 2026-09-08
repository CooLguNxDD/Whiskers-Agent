"""
step_model_settings_store — CRUD for the step model policy.

``server_settings['step_model_policy']`` holds the persisted dict that controls
how GOAP plan steps are assigned LLM models (strategy, maps, overrides) and
whether/ how steps execute in parallel (parallel_enabled, fanout_concurrency).
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

_STEP_MODEL_POLICY_KEY = "step_model_policy"

DEFAULT_POLICY: dict[str, Any] = {
    "strategy": "strength",          # one of: off | strength | task_type | explicit
    "task_type_map": {},             # e.g. {"data_retrieval": "<pool name>", "reasoning": "<pool name>", "formatting": "<pool name>"}
    "op_overrides": {},              # e.g. {"<operation_id>": "<pool name>"}
    "parallel_enabled": True,
    "fanout_concurrency": 5,
}


def _db_available() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


def _fill_from_stored(stored: dict | None) -> dict:
    """Return a deep-copied DEFAULT merged with (possibly partial) stored value; ensure nested dicts are dicts."""
    base = copy.deepcopy(DEFAULT_POLICY)
    if isinstance(stored, dict):
        for k, v in stored.items():
            base[k] = v
    if not isinstance(base.get("task_type_map"), dict):
        base["task_type_map"] = copy.deepcopy(DEFAULT_POLICY["task_type_map"])
    if not isinstance(base.get("op_overrides"), dict):
        base["op_overrides"] = copy.deepcopy(DEFAULT_POLICY["op_overrides"])
    return base


def _apply_patch(base: dict, patch: dict | None) -> dict:
    """Apply patch (with validation/coercion) over a deep copy of base and return result."""
    if not isinstance(patch, dict):
        return copy.deepcopy(base)
    merged = copy.deepcopy(base)
    allowed_strategies = {"off", "strength", "task_type", "explicit"}
    for k, v in patch.items():
        if k == "strategy":
            if isinstance(v, str) and v in allowed_strategies:
                merged[k] = v
            # invalid: ignore (keep value from base)
            continue
        if k == "parallel_enabled":
            merged[k] = bool(v)
            continue
        if k == "fanout_concurrency":
            try:
                n = int(v)
                if n > 0:
                    merged[k] = n
                # non-positive or bad: keep prior
            except (TypeError, ValueError):
                pass
            continue
        # other keys (maps etc.): accept, deepcopy complex
        if isinstance(v, dict):
            merged[k] = copy.deepcopy(v)
        elif isinstance(v, (list, tuple)):
            merged[k] = list(v)
        else:
            merged[k] = v
    return merged


async def _read_raw_value() -> dict | None:
    """Select raw value for the policy key; return dict or None."""
    async with get_async_session() as session:
        row = (await session.execute(
            select(ServerSetting.value).where(ServerSetting.key == _STEP_MODEL_POLICY_KEY)
        )).fetchone()
    if row and row[0] is not None and isinstance(row[0], dict):
        return row[0]
    return None


async def get_step_model_policy() -> dict:
    """Return the step model policy (merged defaults + any stored top-level overrides)."""
    if not _db_available():
        return copy.deepcopy(DEFAULT_POLICY)
    stored = await _read_raw_value()
    if isinstance(stored, dict):
        return _fill_from_stored(stored)
    return copy.deepcopy(DEFAULT_POLICY)


async def set_step_model_policy(patch: dict) -> dict:
    """Merge validated/coerced patch over current policy, persist via upsert when DB available, return result."""
    if not _db_available():
        logger.warning("set_step_model_policy: DB unavailable, change not persisted (in-memory default remains)")
        current = await get_step_model_policy()
        merged = _apply_patch(current, patch)
        return merged

    stored = await _read_raw_value()
    base = _fill_from_stored(stored)
    merged = _apply_patch(base, patch)

    stmt = (
        pg_insert(ServerSetting)
        .values(key=_STEP_MODEL_POLICY_KEY, value=merged)
        .on_conflict_do_update(index_elements=["key"], set_={"value": merged})
    )
    async with get_async_session() as session:
        await session.execute(stmt)
        await session.commit()
    return merged
