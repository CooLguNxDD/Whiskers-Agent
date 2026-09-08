"""
LLM Reranker for fused hybrid-search route candidates.
"""
import asyncio
import logging
from typing import Any

from core.llm_config_service import get_graph_core_llm
from core_graph.node.helpers.mcp_ctx import _parse_json_response
from utils.server_config import (
    RERANK_ENABLED,
    RERANK_TOP_N_IN,
    RERANK_TOP_K_OUT,
    RERANK_TIMEOUT_S,
)

logger = logging.getLogger("whiskers")


def _format_candidate_for_rerank(idx: int, cand: dict) -> str:
    from core_graph.node.helpers.mcp_ctx import _extract_workspace

    op_id = cand.get("operation_id", "unknown")
    desc = (cand.get("description") or "").strip()
    if len(desc) > 120:
        desc = desc[:117] + "..."

    workspace = _extract_workspace(cand)
    ws_str = f" [workspace: {workspace}]" if workspace else ""
    return f"{idx}. {op_id}{ws_str}: {desc}"


async def llm_rerank(
    query: str,
    candidates: list[dict],
    top_k: int = RERANK_TOP_K_OUT,
    *,
    token_usage: dict | None = None,
) -> tuple[list[dict], dict | None]:
    """LLM-based cross-encoder-style rerank of fused hybrid-search candidates.

    Falls back to the input order truncated to top_k on any error, timeout,
    or unparseable response — reranking must never break discovery. Returns
    ``(candidates, token_usage)`` — model selection/escalation routes through
    ``run_role_ladder`` (role "reranker"), closing this call site's previously
    unaccounted token usage. ``token_usage`` (in) is the caller's running
    accumulator to fold into, matching ``fold_token_usage`` elsewhere; the
    returned dict is that same accumulator, unmodified when no LLM call ran.
    """
    if not RERANK_ENABLED or not candidates:
        return candidates[:top_k], token_usage

    pool = candidates[:RERANK_TOP_N_IN]
    if len(pool) <= 1:
        return pool[:top_k], token_usage

    cand_text = "\n".join(_format_candidate_for_rerank(i + 1, c) for i, c in enumerate(pool))

    prompt = f"""You are a tool candidate reranker for an AI agent.
User Query / Goal: "{query}"

Candidate Tools:
{cand_text}

Task: Rank the top candidates (up to {top_k}) that are most relevant and useful for achieving the User Query/Goal.
Return ONLY a valid JSON object in this format:
{{"ranked_indices": [1-based index numbers in order of relevance]}}
Do not include any explanation or extra text."""

    from langchain_core.messages import HumanMessage

    async def _attempt(llm):
        async def _call_llm():
            return await llm.ainvoke([HumanMessage(content=prompt)])

        return await asyncio.wait_for(_call_llm(), timeout=RERANK_TIMEOUT_S)

    try:
        fallback_llm = await get_graph_core_llm()
        from core_graph.model_roles.ladder import run_role_ladder

        res = await run_role_ladder(
            "reranker",
            attempt=_attempt,
            extract=lambda r: _parse_json_response(getattr(r, "content", str(r))),
            fallback_llm=fallback_llm,
            token_usage=token_usage,
        )
        parsed = res.value

        if not parsed or not isinstance(parsed, dict) or "ranked_indices" not in parsed:
            logger.warning("llm_rerank: JSON response missing 'ranked_indices', falling back")
            return candidates[:top_k], res.token_usage

        indices = parsed.get("ranked_indices")
        if not isinstance(indices, list):
            logger.warning("llm_rerank: 'ranked_indices' is not a list, falling back")
            return candidates[:top_k], res.token_usage

        reranked = []
        seen = set()
        unparseable = 0
        for item in indices:
            try:
                idx = int(item) - 1
            except (ValueError, TypeError):
                if unparseable < 3:
                    logger.warning("llm_rerank: unparseable ranked index %r", item)
                unparseable += 1
                continue
            if 0 <= idx < len(pool) and idx not in seen:
                seen.add(idx)
                reranked.append(pool[idx])

        # Backfill remaining slots from original fused pool order
        if len(reranked) < top_k:
            for idx, c in enumerate(pool):
                if idx not in seen:
                    seen.add(idx)
                    reranked.append(c)
                    if len(reranked) >= top_k:
                        break

        return reranked[:top_k], res.token_usage

    except Exception as exc:
        logger.warning("llm_rerank failed or timed out: %s; falling back to fused order", exc)
        return candidates[:top_k], token_usage
