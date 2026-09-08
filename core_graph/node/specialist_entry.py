"""Specialist stack entry — domain pipeline or generic MCP agent, then summary/plan."""

from __future__ import annotations

import logging

from core_graph.node.context import GraphRuntimeContext
from core_graph.runtime.caller_scopes import resolve_caller_scopes
from core_graph.states import DynamicAPIState

logger = logging.getLogger("whiskers.core_graph.specialist")


def make_specialist_entry_node(ctx: GraphRuntimeContext):
    """Run registered domain specialist or generic MCP pipeline.

    On success stamp response for summary. On hard failure without usable
    output, leave response empty and fall through to classic GOAP planning
    (router key ``plan``).
    """

    async def specialist_entry_node(state: DynamicAPIState) -> dict:
        """Execute specialist stack; update triage intent on success or fallback."""
        from core.context import current_tenant_id
        from core_graph.subgraphs.specialist.registry import run_specialist

        raw_query = (
            state.get("original_query")
            or state.get("user_query")
            or ""
        ).strip()
        turn = _parse_visitor_turn(raw_query)
        query = turn.question if turn is not None else raw_query
        plugin_context = _visitor_plugin_context(turn) if turn is not None else None
        goal_class = turn.goal_class if turn is not None else None
        logger.info("specialist_entry: pipeline for query=%r", query[:120])

        try:
            raw_tid = current_tenant_id.get()
            tid = int(raw_tid) if raw_tid is not None else 1
        except Exception:
            tid = 1
        if tid <= 0:
            tid = 1

        # Thread caller scopes into the agent loop. The validated MCP token's
        # scopes (state["caller_scopes"], seeded by mode_router) are the
        # primary source; the request-principal contextvar (playground REST)
        # and stdio-unrestricted are fallbacks. Anonymous HTTP must not
        # resolve to LOCAL_CLI (caller_scopes=None → unrestricted); it gets []
        # so is_allowed fails closed. See core_graph.runtime.caller_scopes.
        caller_scopes = resolve_caller_scopes(state.get("caller_scopes"))

        try:
            envelope = await run_specialist(
                query,
                tenant_id=tid,
                session_id=state.get("session_id"),
                caller_scopes=caller_scopes,
                goal_class=goal_class,
                plugin_context=plugin_context,
            )
        except Exception as exc:
            logger.exception("specialist_entry pipeline failed")
            # Reset to classic: retriage.should_retriage's mode=="specialist" branch
            # requires replan_count>=3 (vs. classic's portfolioish-gated >=2), and its
            # mode=="classic" branch never fires while triage_mode stays "specialist" —
            # leaving it stamped here needlessly delays retriage on a stack the pipeline
            # already failed to run.
            return {
                "triage_mode": "classic",
                "decompose_intent": "specialist_fallback",
                "response": None,
                "last_failure": {
                    "error": "specialist_pipeline_exception",
                    "message": str(exc)[:400],
                },
            }

        if isinstance(envelope, dict) and goal_class and not envelope.get("goal_class"):
            envelope = {**envelope, "goal_class": goal_class}

        status = (envelope or {}).get("status")
        has_layout = isinstance((envelope or {}).get("layout"), dict)
        has_output = (envelope or {}).get("output") is not None
        has_summary = bool((envelope or {}).get("summary") or (envelope or {}).get("message"))
        domain = (envelope or {}).get("specialist_domain") or "generic"
        env_goal_class = (envelope or {}).get("goal_class") or goal_class
        has_ask_overlay = (
            (envelope or {}).get("flow_id") == "portfolio_ask_v1"
            or (envelope or {}).get("blocks") is not None
            or (envelope or {}).get("pending_job") is not None
        )

        # Success / partial with usable payload → summary path
        if status in ("ok", "partial") and (
            has_layout
            or has_output
            or env_goal_class == "discover"
            or has_ask_overlay
            or (status == "ok" and has_summary)
        ):
            summary = envelope.get("summary") or envelope.get("message") or "Specialist finished."
            return {
                "triage_mode": "specialist",
                "decompose_intent": f"specialist_{domain}",
                "response": envelope,
                "summary": summary,
                "goal_loop_decision": "done",
            }

        # Failed compose/agent — fall through to classic GOAP as recovery
        logger.info(
            "specialist_entry: pipeline status=%s domain=%s — falling back to classic plan",
            status,
            domain,
        )
        return {
            "triage_mode": "classic",
            "decompose_intent": "specialist_fallback",
            "response": None,
            "last_failure": {
                "error": "specialist_pipeline_failed",
                "detail": envelope,
            },
            "retriage_context": {
                "reason": (envelope or {}).get("retriage_reason") or "specialist_failed",
                "from_stack": "specialist",
            },
        }

    return specialist_entry_node


def _parse_visitor_turn(user_query: str):
    """CatPortfolio Ask wrapper, or None. Import is fail-open (plugin optional)."""
    try:
        from plugins.portfolio_plugin.ask.visitor_turn import parse_visitor_turn

        return parse_visitor_turn(user_query)
    except Exception:
        return None


def _visitor_plugin_context(turn) -> dict:
    """Board inputs for the ask/bake FlowSpec."""
    from plugins.portfolio_plugin.ask.visitor_turn import visitor_plugin_context

    return visitor_plugin_context(turn)
