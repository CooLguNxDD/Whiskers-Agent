"""Patch quality contract — the bar for a 1-3 block ask overlay.

Deliberately **not** ``bake/contract.py::assess_bake_quality``: that is a
whole-page bar (block count, type diversity, DAG band coverage, jury) and a
two-block patch fails it for reasons that do not apply. ``hard_fail_on_quality``
must not reach this path either — an ask never persists.

Checks what can actually break the client:
  * schema-valid blocks (already enforced upstream, re-asserted cheaply)
  * every patched id was requested — no surprise blocks
  * no reused id changes block type (that silently re-bands the DAG)
  * ``fish.length <= 40`` (the FE caps and the Zod mirror rejects beyond)
  * no ``null`` optionals — CatPortfolio types these ``.optional()``, which
    accepts ``undefined`` but rejects ``null``, and one bad block fails the
    whole discriminatedUnion so the SPA falls back to the master snapshot
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.ask.contract")

MAX_FISH = 40


def _null_paths(node: Any, path: str = "") -> list[str]:
    """Every path under *node* whose value is ``None``."""
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if value is None:
                out.append(here)
            else:
                out.extend(_null_paths(value, here))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            here = f"{path}[{i}]"
            if value is None:
                out.append(here)
            else:
                out.extend(_null_paths(value, here))
    return out


def assess_patch_quality(
    blocks: list[dict[str, Any]] | None,
    *,
    requested_ids: list[str] | None = None,
    base_index: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Judge an ask overlay. Returns ``{ok, errors, warnings, checked}``.

    ``ok=False`` means ship nothing and answer in prose — a patch that would
    break the layout is worse than no patch.
    """
    errors: list[str] = []
    warnings: list[str] = []
    patch = [b for b in (blocks or []) if isinstance(b, dict)]

    if not patch:
        return {"ok": False, "errors": ["no blocks in patch"], "warnings": [], "checked": 0}

    requested = {str(i) for i in (requested_ids or []) if i}
    seen: set[str] = set()
    for block in patch:
        bid = str(block.get("id") or "").strip()
        btype = str(block.get("type") or "").strip()
        if not bid:
            errors.append("block missing id")
            continue
        if not btype:
            errors.append(f"block '{bid}' missing type")
        if bid in seen:
            errors.append(f"duplicate block id '{bid}' in patch")
        seen.add(bid)
        if requested and bid not in requested:
            errors.append(f"block '{bid}' was not a requested target")

        if btype == "fishTank":
            fish = (block.get("props") or {}).get("fish")
            if not isinstance(fish, list) or not fish:
                errors.append("fishTank: no specimens")
            elif len(fish) > MAX_FISH:
                errors.append(f"fishTank: {len(fish)} specimens exceeds cap {MAX_FISH}")

        nulls = _null_paths(block)
        if nulls:
            errors.append(
                f"block '{bid}': null optionals reject the Zod mirror: {nulls[:5]}"
            )

    # A patch replaces by id, so it cannot drop a base block by omission — but
    # it *can* change a block's type under a reused id, which re-bands the DAG
    # and moves unrelated content. That is the real drop risk.
    base_types = {
        str(e.get("id")): str(e.get("type") or "")
        for e in (base_index or [])
        if isinstance(e, dict) and e.get("id")
    }
    for block in patch:
        bid = str(block.get("id") or "").strip()
        btype = str(block.get("type") or "").strip()
        prior = base_types.get(bid)
        if prior and btype and prior != btype:
            errors.append(
                f"block '{bid}' changes type {prior} -> {btype}; that re-bands the DAG"
            )
    if base_types:
        added = sorted(seen - set(base_types))
        if added:
            warnings.append(f"patch inserts new blocks: {added}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checked": len(patch),
    }
