"""
Triage node utilities.
"""
import json
import logging
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage
from core_graph.states import DynamicAPIState
from core_graph.prompts import TRIAGE_PROMPT
from core_graph.prompts.context_block import format_context_block, history_from_messages
from core_graph.goap.goal_loop import reset_turn_fragment
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.helpers import _parse_json_response

logger = logging.getLogger("whiskers")

# Keep the checkpointed transcript bounded; assistant replies now double growth.
_MAX_TRANSCRIPT_MESSAGES = 30


def _trim_transcript(messages: list) -> list:
    """Emit RemoveMessage for all but the most recent N checkpointed messages."""
    if len(messages) <= _MAX_TRANSCRIPT_MESSAGES:
        return []
    removals = []
    for m in messages[:-_MAX_TRANSCRIPT_MESSAGES]:
        mid = getattr(m, "id", None)
        if mid:
            removals.append(RemoveMessage(id=mid))
    return removals


def make_turn_init_node(ctx: GraphRuntimeContext):
    """
    Creates an initial turn node.
    """
    async def turn_init_node(state: DynamicAPIState) -> dict:
        """Initialize or reset per-turn state fragments for the current user interaction."""
        out = reset_turn_fragment(state)
        removals = _trim_transcript(state.get("messages") or [])
        if removals:
            out["messages"] = removals
        return out
    return turn_init_node


def _turn_history(state: DynamicAPIState) -> list[dict]:
    """Project the checkpointed `messages` list into role/content dicts for context_block."""
    return history_from_messages(state.get("messages"))


async def _build_turn_context(state: DynamicAPIState, user_query: str) -> str:
    """Format prior-turn context (history, working memory, last summary) for triage/chat prompts.

    Optionally enriched with cross-session semantic/harness memory when
    `graph.triage.harness_memory_enabled` is on. Memory lookup failures never
    break routing — they just leave the block without the extra snippets.
    """
    ctx_block = format_context_block(
        _turn_history(state), state.get("working_memory"), state.get("last_summary")
    )

    from utils.server_config import TRIAGE_CONFIG
    if TRIAGE_CONFIG.get("harness_memory_enabled") and user_query.strip():
        try:
            from core.context import current_tenant_id
            from core.memory import search_memory

            tenant_id = current_tenant_id.get()
            result = await search_memory(
                user_query, tenant_id=int(tenant_id), top_k=int(TRIAGE_CONFIG.get("harness_memory_top_k", 3))
            )
            hits = result.get("memories") if isinstance(result, dict) else None
            if hits:
                snippets = [str(h.get("content", "")).strip() for h in hits if h.get("content")]
                if snippets:
                    mem_block = "Related memory:\n" + "\n".join(f"- {s}" for s in snippets)
                    ctx_block = f"{ctx_block}\n\n{mem_block}" if ctx_block else mem_block
        except Exception:
            logger.debug("Triage harness-memory lookup failed; continuing without it.", exc_info=True)

    return ctx_block


_VALID_TRIAGE = frozenset({"chat", "classic", "specialist", "task"})
_PORTFOLIO_HINTS = (
    "portfolio",
    "layout.json",
    "design_layout",
    "emit_layout",
    "compose_scoped",
    "bake portfolio",
    "bake_portfolio",
    "patch_job_layout",
    "star story",
    "genui",
    "cat portfolio",
    "catportfolio ask turn",
    "portfolio_ask_v1",
    "scoped_ask",
    "redesign my site",
    "job layout",
    "?j=",
    # career_ops_apply_v1 (job_search_plugin FlowSpec) — same heuristic-fallback
    # gap as portfolio: without a hint here, the LLM triage prompt below is the
    # only path to "specialist", and its own bullet list must also name this.
    "apply to this job",
    "career-ops",
    "career_ops_apply_v1",
)


def _parse_visitor_turn(user_query: str):
    """CatPortfolio Ask wrapper, or None. Import is fail-open (plugin optional)."""
    try:
        from plugins.portfolio_plugin.ask.visitor_turn import parse_visitor_turn

        return parse_visitor_turn(user_query)
    except Exception:
        return None


def normalize_triage_mode(mode: str | None) -> str:
    """Normalize triage mode: task→classic; unknown→classic."""
    m = (mode or "classic").strip().lower()
    if m == "task":
        return "classic"
    if m in ("chat", "classic", "specialist"):
        return m
    return "classic"


def _heuristic_triage(user_query: str) -> str:
    """Keyword fallback when LLM JSON is missing/invalid."""
    q = (user_query or "").lower()
    if any(h in q for h in _PORTFOLIO_HINTS):
        return "specialist"
    return "classic"


def make_triage_node(ctx: GraphRuntimeContext):
    """
    Creates a triage node.
    """
    async def triage_node(state: DynamicAPIState) -> dict:
        """Classify user message as chat | classic | specialist.

        Context-aware: folds prior-turn working_memory / last_summary / messages
        (and optional harness memory). Retriage context is folded when present.

        Model selection routes through ``run_role_ladder`` (role "triage"):
        the escalation cascade (fast SLM -> strong arbiter -> ...) and its
        triggers live in the ``ModelRoleSpec``, not here — this node just
        supplies the prompt and the JSON-mode-validity check.
        """
        from core_graph.model_roles.ladder import run_role_ladder

        user_query = state.get("user_query") or ""
        # CatPortfolio Ask is a known specialist turn — do not let the LLM
        # classify the wrapper as classic tool-research.
        if _parse_visitor_turn(user_query) is not None:
            return {"triage_mode": "specialist"}
        ctx_block = await _build_turn_context(state, user_query)
        messages = [SystemMessage(content=TRIAGE_PROMPT)]
        if ctx_block:
            messages.append(SystemMessage(content=f"Conversation context:\n\n{ctx_block}"))
        retriage_ctx = state.get("retriage_context")
        if retriage_ctx:
            messages.append(
                SystemMessage(
                    content=f"Retriage context:\n{json.dumps(retriage_ctx, default=str)[:2000]}"
                )
            )
        messages.append(HumanMessage(content=user_query))

        res = await run_role_ladder(
            "triage",
            state=state,
            node="triage",
            attempt=lambda llm: llm.ainvoke(messages),
            extract=lambda r: _parse_json_response(r.content),
            fallback_llm=ctx.llm,
            token_usage=state.get("token_usage"),
        )
        if res.status == "ok" and res.value and res.value.get("mode") in _VALID_TRIAGE:
            mode = normalize_triage_mode(res.value["mode"])
        else:
            mode = _heuristic_triage(user_query)
        return {
            "triage_mode": mode,
            "token_usage": res.token_usage,
            "model_audit": [res.audit_entry("triage")],
        }
    return triage_node

def make_chat_node(ctx: GraphRuntimeContext):
    """
    Creates a chat node.
    """
    async def chat_node(state: DynamicAPIState) -> dict:
        """Handle conversational replies, grounded in prior-turn context.

        Folds the same working_memory / last_summary / history (and optional
        harness memory) block used by triage into the reply LLM call, so chat
        answers a context-dependent follow-up instead of a generic greeting.

        Selection only (role "chat") — free-form prose has nothing to
        validate, so no escalation ladder; just picks the role's first rung.
        """
        from utils.telemetry import extract_token_usage
        from core_graph.node.helpers import fold_token_usage

        user_query = state.get("user_query") or ""
        ctx_block = await _build_turn_context(state, user_query)
        messages = []
        if ctx_block:
            messages.append(SystemMessage(content=f"Conversation context:\n\n{ctx_block}"))
        messages.append(HumanMessage(content=user_query))

        llm = await ctx.llm_for_role("chat")
        response = await llm.ainvoke(messages)
        content = response.content
        if isinstance(content, list):
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")

        model_name = str(getattr(llm, "model", getattr(llm, "model_name", "unknown")))
        usage = extract_token_usage(response)
        token_usage = fold_token_usage(state.get("token_usage"), usage, model_name)
        reply = str(content)
        return {
            "response": {"status": "chat", "message": reply},
            "token_usage": token_usage,
            "messages": [AIMessage(content=reply)],
        }
    return chat_node
