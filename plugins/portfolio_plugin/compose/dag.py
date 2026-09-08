"""Open Design matrix DAG banding for GenUI layouts.

Extracted from ``composer.py`` so band tables and stamping stay greppable and
the composer module can shrink without behavior change.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("whiskers.plugins.portfolio.compose.dag")

# Level labels for meta.dag stamping (Open Design level-row contract).
DAG_LEVEL_BY_TYPE: dict[str, tuple[int, str]] = {
    "hero": (0, "Intro"),
    "kpiGrid": (1, "Impact"),
    "statStrip": (1, "Impact"),
    "card": (2, "Projects"),
    "projectGrid": (2, "Projects"),
    "flowAnim": (3, "Architecture"),
    "archDiagram": (3, "Architecture"),
    "mcpSandbox": (3, "Architecture"),
    "scene2d": (3, "Architecture"),
    "fishTank": (3, "Architecture"),
    "chart": (4, "Charts"),
    "comparison": (4, "Charts"),
    "costSim": (4, "Charts"),
    "timeline": (5, "Proof"),
    "starStory": (5, "Proof"),
    "composite": (6, "Deep dive"),
    "codeSnippet": (6, "Deep dive"),
    "prose": (6, "Deep dive"),
    "quickActions": (7, "Ask"),
}

# Peer columns per band. Only bands that deviate from the renderer default
# (min(4, node count)) need an entry.
# L2 Projects: 2 cards per row (was default min(4,N) — too dense for long cards).
# L6 Deep dive: one component per row.
DAG_COLS_BY_LEVEL: dict[int, int] = {2: 2, 6: 1}

# Back-compat private aliases (composer/patch/floor historically used these names).
_DAG_LEVEL_BY_TYPE = DAG_LEVEL_BY_TYPE
_DAG_COLS_BY_LEVEL = DAG_COLS_BY_LEVEL


def stamp_dag_from_blocks(blocks: list[dict]) -> dict | None:
    """Group block ids into Open Design level bands for LayoutRenderer."""
    buckets: dict[int, dict[str, Any]] = {}
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        btype = str(b.get("type") or "")
        bid = str(b.get("id") or "")
        if not bid:
            # Not fatal — LayoutRenderer renders un-DAG'd blocks in the
            # trailing "rest" orphan band — but flag it so it's diagnosable.
            logger.warning(
                "portfolio composer: block at index %d (type=%s) missing 'id', "
                "will render as an orphan (out of matrix story order)",
                i,
                btype,
            )
            continue
        level, label = DAG_LEVEL_BY_TYPE.get(btype, (9, "More"))
        bucket = buckets.setdefault(level, {"level": level, "label": label, "nodes": []})
        # Deep-dive bands must not be multi-column packed by the renderer.
        cols = DAG_COLS_BY_LEVEL.get(level)
        if cols is not None:
            bucket["cols"] = cols
        bucket["nodes"].append(bid)
    if not buckets:
        return None
    levels = [buckets[k] for k in sorted(buckets.keys())]
    # progressive scroll hints (n >= 1 always, by construction above)
    n = max(1, len(levels) - 1)
    for i, lvl in enumerate(levels):
        lvl["at"] = round(i / n, 2)
    return {"levels": levels}


# Back-compat private alias.
_stamp_dag_from_blocks = stamp_dag_from_blocks
