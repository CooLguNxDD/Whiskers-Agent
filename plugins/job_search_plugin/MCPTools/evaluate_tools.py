"""
MCP Tools for evaluating a job offer's fit against applicant resume and preferences.
"""

import os
import json
import re
import logging
from core.context import mcp
from core.llm_provider_management import LLMProvider, get_chat_llm
from langchain_core.messages import SystemMessage, HumanMessage

from db_layer.embeddings.embeddings_core import embed
from plugins.job_search_plugin.store import (
    get_profile,
    search_preferences,
    search_preferences_hybrid,
    add_preference_embedding,
)
from plugins.job_search_plugin.prompts.offer_fit_prompt import OFFER_FIT_SYSTEM_PROMPT

logger = logging.getLogger("whiskers.plugins")


def parse_fit_verdict(raw: str | dict | list | None) -> dict:
    """Coerce LLM output into the verdict dict with safe defaults.

    Accepts a dict, a JSON string (stripping fences), or a LangChain multi-block
    ``.content`` list (``[{"type": "text", "text": "..."}]`` — observed live from
    Gemini) which is joined into a single string first. On parse failure returns
    a dict with recommended=False and a mismatch reason explaining the failure.
    """
    default_dict = {
        "fit_score": 0.0,
        "skill_match_reasons": [],
        "preference_match_reasons": [],
        "mismatch_reasons": ["unparseable verdict"],
        "recommended": False,
    }
    if not raw:
        return default_dict

    if isinstance(raw, list):
        parts = []
        dropped = []
        for block in raw:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
            else:
                dropped.append(block)

        if dropped:
            dropped_types = [
                b.get("type", "unknown") if isinstance(b, dict) else type(b).__name__
                for b in dropped
            ]
            logger.warning("parse_fit_verdict: Dropped %d malformed blocks: %s", len(dropped), dropped_types)

        raw = "".join(parts)
        if not raw:
            return default_dict

    if isinstance(raw, dict):
        parsed = raw
    else:
        text = str(raw).strip()
        parsed = None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass

        if parsed is None:
            # Strip markdown code fences if present
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(1).strip())
                except (json.JSONDecodeError, TypeError):
                    pass

        if parsed is None:
            # Fallback to general curly brace match
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0).strip())
                except (json.JSONDecodeError, TypeError):
                    pass

        if parsed is None or not isinstance(parsed, dict):
            return default_dict

    # Coerce and clamp fit_score [0.0, 1.0]
    try:
        fit_score = float(parsed.get("fit_score", 0.0))
        fit_score = max(0.0, min(1.0, fit_score))
    except (ValueError, TypeError):
        fit_score = 0.0

    # Ensure lists default to []
    skill_match_reasons = parsed.get("skill_match_reasons")
    if not isinstance(skill_match_reasons, list):
        skill_match_reasons = []
    else:
        skill_match_reasons = [str(r) for r in skill_match_reasons]

    preference_match_reasons = parsed.get("preference_match_reasons")
    if not isinstance(preference_match_reasons, list):
        preference_match_reasons = []
    else:
        preference_match_reasons = [str(r) for r in preference_match_reasons]

    mismatch_reasons = parsed.get("mismatch_reasons")
    if not isinstance(mismatch_reasons, list):
        mismatch_reasons = []
    else:
        mismatch_reasons = [str(r) for r in mismatch_reasons]

    # Coerce recommended to bool
    recommended = parsed.get("recommended")
    if not isinstance(recommended, bool):
        recommended = bool(recommended)

    return {
        "fit_score": fit_score,
        "skill_match_reasons": skill_match_reasons,
        "preference_match_reasons": preference_match_reasons,
        "mismatch_reasons": mismatch_reasons,
        "recommended": recommended,
    }


@mcp.tool(
    title="index_preferences",
    tags={"job_search_plugin", "write"},
    annotations={"readOnlyHint": False, "idempotentHint": False},
)
async def index_preferences(
    applicant_profile_id: int,
    preferences_text: str = "",
) -> dict:
    """Index applicant's resume and preferences into embeddings.

    Embeds via the same resolved (job_search_plugin, index_preferences) model
    selection that search_preferences_hybrid queries against, and stamps each
    row's model column with model_id_for(sel) — search_engine.search()'s
    hybrid path filters candidate rows by exact model_col match, so a row
    stored under a different/blank model id is invisible to hybrid retrieval
    even though it's still found by the plain dense-cosine path (which has no
    model filter). Mirrors db_layer/search_content_vectors_store.py's index
    side, the pattern every other hybrid-search adapter in this codebase uses.
    """
    from core.llm_config_service import resolve_tool_embedding
    from db_layer.embeddings.embeddings_core import embed_query_with, model_id_for

    profile = await get_profile(applicant_profile_id)
    if not profile:
        return {"status": "error", "error": "profile_not_found"}

    resume_text = profile.get("base_resume_text") or ""
    pref_text = preferences_text if preferences_text else (profile.get("preferences_text") or "")

    resume_chunks = [p.strip() for p in resume_text.split("\n\n") if p.strip()]
    pref_chunks = [p.strip() for p in pref_text.split("\n\n") if p.strip()]

    sel = await resolve_tool_embedding("job_search_plugin", "index_preferences")
    model_id = model_id_for(sel)

    indexed_count = 0
    for chunk in resume_chunks:
        vec = await embed_query_with(sel, chunk)
        await add_preference_embedding(applicant_profile_id, "resume", chunk, vec, model=model_id)
        indexed_count += 1

    for chunk in pref_chunks:
        vec = await embed_query_with(sel, chunk)
        await add_preference_embedding(applicant_profile_id, "preferences", chunk, vec, model=model_id)
        indexed_count += 1

    return {
        "status": "ok",
        "indexed": indexed_count,
        "sources": {
            "resume": len(resume_chunks),
            "preferences": len(pref_chunks),
        },
    }


@mcp.tool(
    title="evaluate_offer_fit",
    tags={"job_search_plugin", "read"},
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def evaluate_offer_fit(
    applicant_profile_id: int,
    offer_text: str,
    hybrid: bool = True,
    top_k: int = 8,
) -> dict:
    """Evaluate a fetched job offer's fit against applicant preferences and resume chunks.

    hybrid=True (default) retrieves via the dense+sparse (RRF) adapter over
    db_layer/embeddings/search_engine.py — the single hybrid-search choke point.
    hybrid=False forces the pre-Stage-4 dense-only cosine path. No per-call
    vector/BM25 weight tuning — the engine's RRF fusion is not a tunable
    linear blend; see Stage 4 decision record.
    """
    if hybrid:
        chunks = await search_preferences_hybrid(applicant_profile_id, offer_text, top_k=top_k)
    else:
        qvec = await embed(offer_text)
        chunks = await search_preferences(applicant_profile_id, qvec, top_k=top_k)

    resume_chunks = [c for c in chunks if c.get("source") == "resume"]
    pref_chunks = [c for c in chunks if c.get("source") == "preferences"]

    human_parts = []
    human_parts.append("### Retrieved Applicant Resume Chunks:")
    if resume_chunks:
        for idx, chunk in enumerate(resume_chunks, 1):
            similarity = chunk.get("similarity")
            sim_str = f" (Similarity: {similarity:.4f})" if similarity is not None else ""
            human_parts.append(f"Chunk {idx}{sim_str}:\n{chunk['content']}")
    else:
        human_parts.append("No matching resume chunks found.")

    human_parts.append("### Retrieved Applicant Preference Chunks:")
    if pref_chunks:
        for idx, chunk in enumerate(pref_chunks, 1):
            similarity = chunk.get("similarity")
            sim_str = f" (Similarity: {similarity:.4f})" if similarity is not None else ""
            human_parts.append(f"Chunk {idx}{sim_str}:\n{chunk['content']}")
    else:
        human_parts.append("No matching preference chunks found.")

    human_parts.append(f"### Job Offer:\n{offer_text}")
    human_content = "\n\n".join(human_parts)

    provider_str = os.environ.get("LLM_PROVIDER", "openai").lower()
    try:
        provider = LLMProvider(provider_str)
    except ValueError:
        provider = LLMProvider.OPENAI

    model = os.environ.get("LLM_MODEL", "")
    llm = get_chat_llm(provider, model)

    resp = await llm.ainvoke([
        SystemMessage(content=OFFER_FIT_SYSTEM_PROMPT),
        HumanMessage(content=human_content),
    ])

    verdict = parse_fit_verdict(resp.content)

    retrieved = [
        {
            "source": c.get("source"),
            "content": c.get("content"),
            "similarity": c.get("similarity"),
        }
        for c in chunks
    ]

    return {
        "status": "ok",
        "verdict": verdict,
        "retrieved": retrieved,
        "hybrid_used": hybrid,
    }
