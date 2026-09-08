"""
Embedder node utilities.
"""
import asyncio
from core_graph.states import DynamicAPIState
from core_graph.node.context import GraphRuntimeContext
from utils.server_config import CANDIDATE_TOP_K, CANDIDATE_POOL_MAX

def make_embedder_node(ctx: GraphRuntimeContext):
    """
    Creates an embedder node.
    """
    async def embedder_node(state: DynamicAPIState) -> dict:
        """Embed sub-tasks / goal / user query → pgvector top-k per intent → candidate routes."""
        from db_layer.embeddings.embeddings_routes import search_routes

        intents = []
        # Decompose-first pipeline: prefer the decomposed sub-tasks so each
        # sub-task gets its own retrieval pass instead of one blended query.
        sub_tasks = state.get("sub_tasks") or []
        if sub_tasks:
            intents.extend(s for s in sub_tasks if isinstance(s, str) and s.strip())
        if not intents and state.get("goal"):
            goal_val = state["goal"]
            if isinstance(goal_val, list):
                intents.extend(goal_val)
            elif isinstance(goal_val, str):
                intents.extend([g.strip() for g in goal_val.split("\n") if g.strip()])
        
        if not intents:
            intents = [state.get("user_query") or ""]

        tasks = [search_routes(intent, top_k=CANDIDATE_TOP_K) for intent in intents]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            results = []

        all_candidates = []
        for res in results:
            if isinstance(res, list):
                all_candidates.extend(res)

        # Sort all new candidates by score descending to keep best matches first
        all_candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)

        candidate_pool = list(state.get("candidate_pool") or [])
        seen_ops = {c.get("operation_id") for c in candidate_pool if c.get("operation_id")}

        # Cap the merged pool: pre-existing pool entries are never evicted (plans
        # may reference them mid-loop); only the highest-score NEW candidates are
        # admitted up to CANDIDATE_POOL_MAX so multi-sub-task retrieval can't
        # blow up the planner prompt.
        merged_candidates = list(candidate_pool)
        for c in all_candidates:
            if len(merged_candidates) >= max(CANDIDATE_POOL_MAX, len(candidate_pool)):
                break
            op_id = c.get("operation_id")
            if op_id and op_id not in seen_ops:
                seen_ops.add(op_id)
                merged_candidates.append(c)

        candidates = merged_candidates

        # Hydrate empty parameters from live RouteRegistry (proxy tools store
        # schemas on Tool.parameters; stale route_embeddings rows may be {}).
        if candidates and ctx.route_registry is not None:
            from core_graph.node.helpers import _descriptor_to_candidate
            for c in candidates:
                if c.get("parameters"):
                    continue
                try:
                    desc = ctx.route_registry.get(c["operation_id"], c.get("plugin_id"))
                except Exception:
                    desc = None
                if desc and desc.parameters:
                    c["parameters"] = desc.parameters
                    
        # Sort merged candidates by score
        candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        
        best_score = candidates[0].get("score", 0.0) if candidates else 0.0

        goal_val = state.get("goal")
        if isinstance(goal_val, list):
            primary_query = " ".join(goal_val)
        elif isinstance(goal_val, str) and goal_val.strip():
            primary_query = goal_val.strip()
        else:
            primary_query = state.get("user_query") or ""

        # Rerank reorders for prompt-display quality only — GOAP search/validation
        # downstream (planner.py) relies on `candidates` holding the FULL pool
        # (see test_embedder_subtasks.py pool-cap contract tests), so we fold
        # the reranked top-K back to the front rather than truncating the list.
        full_pool = candidates
        from core_graph.goap.reranker import llm_rerank
        from utils.server_config import RERANK_TOP_K_OUT
        reranked_top, rerank_usage = await llm_rerank(
            primary_query, full_pool, top_k=RERANK_TOP_K_OUT, token_usage=state.get("token_usage"),
        )
        reranked_ops = {c.get("operation_id") for c in reranked_top if c.get("operation_id")}
        remainder = [c for c in full_pool if c.get("operation_id") not in reranked_ops]
        candidates = reranked_top + remainder

        return {
            "candidates": candidates,
            "candidate_pool": full_pool,
            "confidence": best_score,
            "token_usage": rerank_usage,
        }
    return embedder_node

