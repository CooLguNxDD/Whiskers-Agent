"""Hex leasing for multi-agent conflict prevention (step 5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from core.context import mcp
from db_layer.connection import get_async_session


@mcp.tool(
    title="claim_hexes",
    tags={"world_semantic_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def claim_hexes(
    world_id: str,
    hexes: list[str],
    agent_id: str,
    ttl_seconds: int = 120,
) -> dict:
    """Atomically claim hexes. Partial failure → release all claimed in this call + report conflicts."""
    if not world_id or not agent_id or not hexes:
        return {
            "status": "error",
            "error": "missing_required_fields",
            "missing_fields": [
                n
                for n, v in (("world_id", world_id), ("agent_id", agent_id), ("hexes", hexes))
                if not v
            ],
        }

    ttl = max(5, min(int(ttl_seconds), 3600))
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(seconds=ttl)
    claimed: list[str] = []
    conflicts: list[dict] = []

    async with get_async_session() as session:
        for hid in hexes:
            result = await session.execute(
                text(
                    """
                    UPDATE hexes SET
                        lease_holder = :agent,
                        lease_expiry = :expiry,
                        updated_at = now()
                    WHERE world_id = :w AND hex_id = :h
                      AND (
                        lease_holder IS NULL
                        OR lease_expiry IS NULL
                        OR lease_expiry < :now
                        OR lease_holder = :agent
                      )
                    RETURNING hex_id
                    """
                ),
                {
                    "agent": agent_id,
                    "expiry": expiry,
                    "w": world_id,
                    "h": hid,
                    "now": now,
                },
            )
            row = result.first()
            if row:
                claimed.append(hid)
            else:
                cur = await session.execute(
                    text(
                        """
                        SELECT lease_holder, lease_expiry FROM hexes
                        WHERE world_id = :w AND hex_id = :h
                        """
                    ),
                    {"w": world_id, "h": hid},
                )
                info = cur.mappings().first()
                conflicts.append(
                    {
                        "hex_id": hid,
                        "lease_holder": (info or {}).get("lease_holder"),
                        "lease_expiry": str((info or {}).get("lease_expiry")),
                        "error": "hex_not_found" if not info else "lease_held",
                    }
                )

        if conflicts:
            # release-all claimed in this call
            if claimed:
                await session.execute(
                    text(
                        """
                        UPDATE hexes SET lease_holder = NULL, lease_expiry = NULL, updated_at = now()
                        WHERE world_id = :w AND hex_id = ANY(:ids) AND lease_holder = :agent
                        """
                    ),
                    {"w": world_id, "ids": claimed, "agent": agent_id},
                )
            await session.commit()
            return {
                "status": "error",
                "error": "partial_claim_failed",
                "released": claimed,
                "conflicts": conflicts,
            }

        await session.commit()

    return {
        "status": "ok",
        "world_id": world_id,
        "agent_id": agent_id,
        "claimed": claimed,
        "lease_expiry": expiry.isoformat(),
        "ttl_seconds": ttl,
    }


@mcp.tool(
    title="release_hexes",
    tags={"world_semantic_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def release_hexes(world_id: str, hexes: list[str], agent_id: str) -> dict:
    """Release leases held by agent_id."""
    if not world_id or not agent_id or not hexes:
        return {"status": "error", "error": "missing_required_fields"}

    async with get_async_session() as session:
        result = await session.execute(
            text(
                """
                UPDATE hexes SET lease_holder = NULL, lease_expiry = NULL, updated_at = now()
                WHERE world_id = :w AND hex_id = ANY(:ids) AND lease_holder = :agent
                RETURNING hex_id
                """
            ),
            {"w": world_id, "ids": list(hexes), "agent": agent_id},
        )
        released = [r[0] for r in result.fetchall()]
        await session.commit()
    return {"status": "ok", "released": released, "agent_id": agent_id}


@mcp.tool(
    title="renew_lease",
    tags={"world_semantic_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": True},
)
async def renew_lease(
    world_id: str,
    hexes: list[str],
    agent_id: str,
    ttl_seconds: int = 120,
) -> dict:
    """Extend lease expiry for hexes currently held by agent_id."""
    if not world_id or not agent_id or not hexes:
        return {"status": "error", "error": "missing_required_fields"}
    ttl = max(5, min(int(ttl_seconds), 3600))
    expiry = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    async with get_async_session() as session:
        result = await session.execute(
            text(
                """
                UPDATE hexes SET lease_expiry = :expiry, updated_at = now()
                WHERE world_id = :w AND hex_id = ANY(:ids) AND lease_holder = :agent
                RETURNING hex_id
                """
            ),
            {"w": world_id, "ids": list(hexes), "agent": agent_id, "expiry": expiry},
        )
        renewed = [r[0] for r in result.fetchall()]
        await session.commit()
    return {
        "status": "ok",
        "renewed": renewed,
        "lease_expiry": expiry.isoformat(),
    }
