"""CRUD for global harness instructions in server_settings.

Key: ``harness_instructions`` — instance-level JSON document (not per-tenant).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db_layer.connection import get_async_session
from db_layer.models import ServerSetting

logger = logging.getLogger("whiskers.memory")

HARNESS_INSTRUCTIONS_KEY = "harness_instructions"

_EMPTY_DOC: dict[str, Any] = {
    "version": 1,
    "updated_at": None,
    "seeded": False,
    "items": [],
}


def _db_available() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def empty_harness_doc() -> dict[str, Any]:
    """Return a fresh empty harness instructions document."""
    return dict(_EMPTY_DOC, items=[])


async def get_harness_instructions_doc() -> dict[str, Any]:
    """Load harness instructions JSON; empty skeleton if missing / no DB."""
    if not _db_available():
        return empty_harness_doc()
    try:
        async with get_async_session() as session:
            row = (
                await session.execute(
                    select(ServerSetting.value).where(
                        ServerSetting.key == HARNESS_INSTRUCTIONS_KEY
                    )
                )
            ).fetchone()
        if row and isinstance(row[0], dict):
            doc = dict(row[0])
            if not isinstance(doc.get("items"), list):
                doc["items"] = []
            doc.setdefault("version", 1)
            doc.setdefault("seeded", False)
            return doc
    except Exception as exc:
        logger.warning("get_harness_instructions_doc failed: %s", exc)
    return empty_harness_doc()


async def set_harness_instructions_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Persist the full harness instructions document."""
    value = {
        "version": int(doc.get("version") or 1),
        "updated_at": doc.get("updated_at") or _now_iso(),
        "seeded": bool(doc.get("seeded", False)),
        "items": list(doc.get("items") or []),
    }
    if not _db_available():
        logger.warning("set_harness_instructions_doc: DB unavailable, not persisted")
        return value
    stmt = (
        pg_insert(ServerSetting)
        .values(key=HARNESS_INSTRUCTIONS_KEY, value=value)
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value},
        )
    )
    async with get_async_session() as session:
        await session.execute(stmt)
        await session.commit()
    return value
