"""Resolve a (block_type, slug) intent to a real block id (pure / sync).

Ask mode addresses blocks semantically because baked layouts carry ids the
layout agent invented (``proj-ai``, ``card-helix-devops-infra``). The client
ships a *block index* — ``[{"id", "type", "slug"?}]`` — instead of the whole
layout, and this module maps an intent onto it. No migration of existing
``?j=`` rows is needed.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.ask.targets")

# Page-level furniture an ask turn must never rewrite. Only a real bake owns
# these — a visitor question that silently rewrote the hero would read as the
# page resetting itself.
SACRED_BLOCK_TYPES = frozenset({"hero", "kpiGrid", "statStrip", "quickActions"})

# Types that exist at most once per layout, so a slug-less intent can resolve
# to "the" block of that type.
SINGLETON_BLOCK_TYPES = frozenset({"fishTank", "hero", "kpiGrid", "statStrip", "quickActions"})

# ``card-<slug>`` / ``prose-<slug>`` — the same prefix convention CatPortfolio's
# ``fishFromLayout.ts`` strips when it derives fish from card blocks.
_ID_PREFIX_RE = re.compile(r"^(?P<prefix>[a-zA-Z]+[a-zA-Z0-9]*)-(?P<slug>.+)$")

_SLUG_SAFE_RE = re.compile(r"[^a-z0-9-]+")


def canonical_block_id(block_type: str, slug: str) -> str:
    """Deterministic id for a newly inserted block, e.g. ``card-my-project``."""
    btype = str(block_type or "block").strip() or "block"
    clean = _SLUG_SAFE_RE.sub("-", str(slug or "").strip().lower()).strip("-")
    return f"{btype}-{clean}" if clean else f"{btype}-1"


def slug_of_entry(entry: dict[str, Any]) -> str:
    """Best-effort slug for one block-index entry: explicit prop, else id suffix."""
    if not isinstance(entry, dict):
        return ""
    explicit = str(entry.get("slug") or "").strip()
    if explicit:
        return explicit.lower()
    match = _ID_PREFIX_RE.match(str(entry.get("id") or "").strip())
    if not match:
        return ""
    # Only treat the suffix as a slug when the prefix names the block type,
    # so an agent id like ``h1`` or ``arch-floor`` doesn't invent a project.
    prefix = match.group("prefix").lower()
    if prefix != str(entry.get("type") or "").strip().lower():
        return ""
    return match.group("slug").strip().lower()


def resolve_targets(
    block_index: list[dict[str, Any]] | None,
    block_type: str,
    slug: str = "",
    *,
    allow_sacred: bool = False,
) -> list[str]:
    """Block ids in *block_index* matching ``(block_type, slug)``.

    Resolution order: exact ``id`` match on the canonical id → slug match on the
    entry (explicit ``slug`` prop or ``<type>-<slug>`` id) → first-of-type for a
    singleton type when no slug was requested. Returns ``[]`` on no match, which
    the caller reads as "insert a new block".
    """
    btype = str(block_type or "").strip()
    if not btype:
        return []
    if btype in SACRED_BLOCK_TYPES and not allow_sacred:
        logger.debug("ask targets: refusing sacred block type %s", btype)
        return []

    entries = [e for e in (block_index or []) if isinstance(e, dict)]
    of_type = [e for e in entries if str(e.get("type") or "").strip() == btype]
    if not of_type:
        return []

    slug_n = str(slug or "").strip().lower()
    if slug_n:
        canonical = canonical_block_id(btype, slug_n)
        exact = [
            str(e.get("id"))
            for e in of_type
            if str(e.get("id") or "").strip() == canonical
        ]
        if exact:
            return exact
        by_slug = [
            str(e.get("id"))
            for e in of_type
            if slug_of_entry(e) == slug_n and e.get("id")
        ]
        if by_slug:
            return by_slug
        return []

    if btype in SINGLETON_BLOCK_TYPES:
        first = next((str(e.get("id")) for e in of_type if e.get("id")), "")
        return [first] if first else []
    return []


def tank_entry(block_index: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The fishTank entry from a block index, or None when the layout has no tank."""
    for entry in block_index or []:
        if isinstance(entry, dict) and str(entry.get("type") or "").strip() == "fishTank":
            return entry
    return None
