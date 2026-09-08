"""Short-id allocation for baked layouts.

Persistence of the row itself stays with the bake tool, which owns the response
contract; this module owns the one piece with retry semantics worth isolating.
"""

from __future__ import annotations

import logging

from plugins.portfolio_plugin.store import job_layout_short_id_exists
from utils.short_id import generate_short_id

logger = logging.getLogger("whiskers.plugins.portfolio.bake_tools")

MAX_SHORT_ID_ATTEMPTS = 5


async def allocate_short_id(*seed_parts: str) -> str | None:
    """Find an unused public short id from these seed parts, or None on collision.

    Two call sites seed differently — a fresh bake uses ``(company, role)``, a
    derived patch uses the parent id plus ``"patch"`` — so the seed is variadic
    rather than fixed.
    """
    for _ in range(MAX_SHORT_ID_ATTEMPTS):
        candidate = generate_short_id(list(seed_parts))
        if not await job_layout_short_id_exists(candidate):
            return candidate
    return None
