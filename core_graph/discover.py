"""Gateway discover-mode helpers (candidate ranking without execution).

Extracted from ``mcp_tool`` so the discover CSV path can evolve without
touching graph invocation. Re-exported via ``core_graph.mcp_tool``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("whiskers")


async def discover_candidates_impl(
    user_message: str,
    top_k: int | None = None,
    *,
    db_available: bool = True,
    tools_to_csv=None,
) -> dict[str, Any]:
    """Embedder-only dry run: rank candidate tools for a prompt, NO execution.

    Used by the gateway discover flow — a client whose direct call failed can
    ask which underlying tools match its intent, inspect their definitions, and
    confirm before issuing a real ``run_graph`` (execute) call.
    """
    if not db_available:
        return {
            "status": "unavailable",
            "message": "discover requires DATABASE_URL (pgvector route embeddings).",
        }
    from db_layer.embeddings.embeddings_routes import search_routes
    from core_graph.node.helpers import normalize_tool_card
    from utils.server_config import CANDIDATE_TOP_K
    from core_graph.runtime.envelopes import _tools_to_csv as _default_csv

    to_csv = tools_to_csv or _default_csv
    k = top_k or CANDIDATE_TOP_K
    try:
        candidates = await search_routes(user_message, top_k=k)
    except Exception as exc:
        logger.exception("discover candidate search failed")
        return {"status": "error", "message": str(exc)}

    tools = [
        {
            **normalize_tool_card(c),
            "score": round(float(c.get("score", 0.0)), 3),
        }
        for c in candidates
    ]
    return {
        "status": "ok",
        "mode": "discover",
        "count": len(tools),
        "tools": to_csv(tools),
        "next": "Confirm the intended tool, then call run_graph with mode='execute' (default).",
    }
