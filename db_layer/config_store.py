"""
config_store — data-access helpers for server_settings LLM config.

Route handlers in api/config_routes.py own HTTP shaping; this module owns
reads/writes of the ``llm`` key under server_settings via the ServerSetting ORM.
"""

from __future__ import annotations

import logging
from typing import Any

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db_layer.connection import get_async_session
from db_layer.models import ServerSetting

logger = logging.getLogger("whiskers")

_LLM_SETTING_KEY = "llm"


async def get_llm_settings(
    allowed_keys: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Return LLM settings from server_settings (empty dict if absent).

    When ``allowed_keys`` is provided, only those keys are returned; otherwise
    the full stored blob is returned (used for merge-on-write).
    """
    async with get_async_session() as session:
        row = (await session.execute(
            select(ServerSetting.value).where(ServerSetting.key == _LLM_SETTING_KEY)
        )).fetchone()
    if not row or not row[0]:
        return {}
    raw = row[0]
    if not isinstance(raw, dict):
        return {}
    if allowed_keys is None:
        return dict(raw)
    return {k: v for k, v in raw.items() if k in allowed_keys}


async def upsert_llm_settings(merged: dict[str, Any]) -> None:
    """Persist the full merged LLM settings dict under server_settings['llm']."""
    now = datetime.now(timezone.utc)
    stmt = (
        pg_insert(ServerSetting)
        .values(key=_LLM_SETTING_KEY, value=merged, updated_at=now)
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"value": merged, "updated_at": now},
        )
    )
    async with get_async_session() as session:
        await session.execute(stmt)
        await session.commit()
    logger.info("config_store: llm settings upserted (%d keys)", len(merged))
