"""Global harness instructions — DB JSON document + seed defaults.

Source of truth: ``server_settings['harness_instructions']``.
Seed files under ``core/memory/seed_instructions/`` are used only when empty.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from db_layer.harness_instructions_store import (
    empty_harness_doc,
    get_harness_instructions_doc,
    set_harness_instructions_doc,
)

logger = logging.getLogger("whiskers.memory")

_SEED_DIR = Path(__file__).resolve().parent / "seed_instructions"
_ID_RE = re.compile(r"[^a-zA-Z0-9_]+")


def _slug(text: str) -> str:
    s = _ID_RE.sub("_", (text or "").strip().lower()).strip("_")
    return s[:80] or "instruction"


def _load_seed_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not _SEED_DIR.is_dir():
        return items
    files = sorted(
        _SEED_DIR.glob("*.md"),
        key=lambda p: (0 if p.name.startswith("collection") else 1, p.name),
    )
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    for path in files:
        try:
            body = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("harness seed: cannot read %s: %s", path, exc)
            continue
        if not body:
            continue
        iid = path.stem
        # First markdown H1 as title if present
        title = iid.replace("_", " ").title()
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        items.append(
            {
                "id": iid,
                "title": title,
                "body": body,
                "tags": ["harness", "seed"],
                "source": "seed",
                "updated_at": now,
            }
        )
    return items


async def seed_from_defaults(*, force: bool = False) -> dict[str, Any]:
    """Seed DB from disk defaults when empty or force=True."""
    doc = await get_harness_instructions_doc()
    if doc.get("seeded") and doc.get("items") and not force:
        return doc
    if doc.get("items") and not force:
        # User-edited but never marked seeded
        doc["seeded"] = True
        return await set_harness_instructions_doc(doc)

    items = _load_seed_items()
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "version": 1,
        "updated_at": now,
        "seeded": True,
        "items": items,
    }
    return await set_harness_instructions_doc(doc)


async def get_harness_instructions(*, ensure_seeded: bool = True) -> dict[str, Any]:
    """Load instructions; seed from defaults on first use when empty."""
    doc = await get_harness_instructions_doc()
    if ensure_seeded and (not doc.get("items") or not doc.get("seeded")):
        try:
            doc = await seed_from_defaults(force=False)
        except Exception as exc:
            logger.warning("harness seed failed: %s", exc)
            # Offline fallback: in-memory seed items
            if not doc.get("items"):
                doc = empty_harness_doc()
                doc["items"] = _load_seed_items()
                doc["seeded"] = True
    return doc


def format_instructions_text(doc: dict[str, Any] | None = None) -> str:
    """Join instruction bodies for the planner harness block (sync helper)."""
    if doc is None:
        return ""
    parts: list[str] = []
    for item in doc.get("items") or []:
        if not isinstance(item, dict):
            continue
        body = (item.get("body") or "").strip()
        if body:
            parts.append(body)
    return "\n\n".join(parts)


async def format_instructions_text_async(*, ensure_seeded: bool = True) -> str:
    """Load + format instruction bodies for the planner."""
    doc = await get_harness_instructions(ensure_seeded=ensure_seeded)
    return format_instructions_text(doc)


async def upsert_instruction(
    instruction_id: str,
    body: str,
    *,
    title: str = "",
    tags: list[str] | None = None,
    source: str = "tool",
) -> dict[str, Any]:
    """Insert or replace a single instruction item in the global JSON doc."""
    body_s = (body or "").strip()
    if not body_s:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": ["body"],
        }
    iid = _slug(instruction_id or title or body_s[:40])
    doc = await get_harness_instructions(ensure_seeded=True)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    items = [i for i in (doc.get("items") or []) if isinstance(i, dict)]
    found = False
    for i, item in enumerate(items):
        if item.get("id") == iid:
            items[i] = {
                **item,
                "title": (title or item.get("title") or iid).strip(),
                "body": body_s,
                "tags": list(tags if tags is not None else item.get("tags") or []),
                "source": source,
                "updated_at": now,
            }
            found = True
            break
    if not found:
        items.append(
            {
                "id": iid,
                "title": (title or iid).strip(),
                "body": body_s,
                "tags": list(tags or ["harness"]),
                "source": source,
                "updated_at": now,
            }
        )
    doc["items"] = items
    doc["updated_at"] = now
    doc["seeded"] = True
    saved = await set_harness_instructions_doc(doc)
    return {
        "status": "ok",
        "kind": "harness_instruction",
        "id": iid,
        "count": len(saved.get("items") or []),
    }


async def delete_instruction(instruction_id: str) -> dict[str, Any]:
    """Remove an instruction item by id."""
    iid = _slug(instruction_id)
    doc = await get_harness_instructions(ensure_seeded=False)
    items = [
        i
        for i in (doc.get("items") or [])
        if isinstance(i, dict) and i.get("id") != iid
    ]
    if len(items) == len(doc.get("items") or []):
        return {"status": "error", "error": "instruction_not_found", "id": iid}
    from datetime import datetime, timezone

    doc["items"] = items
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    await set_harness_instructions_doc(doc)
    return {"status": "ok", "deleted": True, "id": iid}
