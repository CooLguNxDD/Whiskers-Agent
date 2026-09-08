"""
Decompose node utilities.

Runs before the embedder (decompose-first pipeline): one small LLM pass splits the
user request into single-action sub-tasks so retrieval can embed each sub-task
independently, plus preliminary seed literal extraction.
"""
import logging
import time
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from core_graph.states import DynamicAPIState
from core_graph.prompts import DECOMPOSE_PROMPT
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.helpers import _parse_json_response, format_replan_context
from utils.server_config import MAX_SUBTASKS

logger = logging.getLogger("whiskers")

# Tools-catalog summary cache: the catalog only changes on plugin hot-swap, so a
# short TTL keeps decompose from hitting the DB every turn while staying fresh.
_TOOLS_SUMMARY_TTL_S = 300
_tools_summary_cache: tuple[float, str] | None = None


async def _get_tools_summary() -> str:
    """Fetch (TTL-cached) the compact one-line-per-tool catalog overview."""
    global _tools_summary_cache
    now = time.monotonic()
    if _tools_summary_cache and now - _tools_summary_cache[0] < _TOOLS_SUMMARY_TTL_S:
        return _tools_summary_cache[1]
    try:
        from db_layer.gateway_settings_store import build_tools_summary
        summary = await build_tools_summary(limit=40) or ""
    except Exception as exc:
        logger.warning("decompose: build_tools_summary failed: %s", exc)
        summary = ""
    _tools_summary_cache = (now, summary)
    return summary


def make_decompose_node(ctx: GraphRuntimeContext):
    """
    Creates a decompose node.
    """
    async def decompose_node(state: DynamicAPIState) -> dict:
        """LLM splits the user request into sub-tasks for per-intent candidate retrieval."""
        user_query = state.get("user_query") or ""
        fallback = {"sub_tasks": [user_query], "decompose_intent": None, "decompose_seed_values": {}}
        try:
            from core_graph.prompts.context_block import format_context_block, history_from_messages
            history = history_from_messages(state.get("messages"))
            ctx_block = format_context_block(history, state.get("working_memory"), state.get("last_summary"))
            rc = format_replan_context(state.get("replan_context") or [])
            remaining_facts = state.get("remaining_goal_facts") or []
            focus = (
                f"Remaining sub-goals to achieve (decompose only this gap): {remaining_facts}\n\n"
                if remaining_facts else ""
            )
            catalog = await _get_tools_summary()
            catalog_block = f"Tool catalog overview (names indicative, not exhaustive):\n{catalog}\n\n" if catalog else ""

            msgs = [
                SystemMessage(content=DECOMPOSE_PROMPT),
                HumanMessage(content=f"{ctx_block}\n\n{rc}\n\n{focus}{catalog_block}User request: {user_query}"),
            ]
            from core_graph.model_roles.ladder import run_role_ladder

            res = await run_role_ladder(
                "decompose",
                state=state,
                node="decompose",
                attempt=lambda llm: llm.ainvoke(msgs),
                extract=lambda r: _parse_json_response(r.content),
                fallback_llm=ctx.llm,
                token_usage=state.get("token_usage"),
            )
            parsed = res.value if res.status == "ok" else None
            if not parsed:
                return {**fallback, "token_usage": res.token_usage, "model_audit": [res.audit_entry("decompose")]}

            sub_tasks = [s.strip() for s in parsed.get("sub_tasks") or [] if isinstance(s, str) and s.strip()]
            if not sub_tasks:
                return {**fallback, "token_usage": res.token_usage, "model_audit": [res.audit_entry("decompose")]}
            intent = parsed.get("intent") if isinstance(parsed.get("intent"), str) else None
            seed_values = parsed.get("seed_values") if isinstance(parsed.get("seed_values"), dict) else {}

            return {
                "sub_tasks": sub_tasks[:MAX_SUBTASKS],
                "decompose_intent": intent,
                "decompose_seed_values": seed_values,
                "token_usage": res.token_usage,
                "model_audit": [res.audit_entry("decompose")],
                "messages": [AIMessage(content=intent or "Decomposed request into sub-tasks.", additional_kwargs={"internal": True})],
            }
        except Exception as exc:
            # Never gate/halt here — the planner's confidence gate stays authoritative.
            logger.warning("decompose: falling back to raw user_query as single sub-task: %s", exc)
            return fallback
    return decompose_node
