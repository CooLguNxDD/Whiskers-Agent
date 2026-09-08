"""
Confirmation and clarification nodes.
"""
import logging
from langchain_core.messages import HumanMessage
from core_graph.states import DynamicAPIState
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.helpers import _current_mcp_context, _elicit

logger = logging.getLogger("whiskers")

def make_confirm_node(ctx: GraphRuntimeContext):
    """
    Creates a confirmation node.
    """
    async def confirm_node(state: DynamicAPIState) -> dict:
        """Render a confirmation prompt covering every step in the plan."""
        plan = state.get("plan") or []
        steps = plan or state.get("instruction_set") or []
        if len(steps) > 1:
            lines = [state.get("clarification_question") or "Confirm multi-step plan:"]
            for i, step in enumerate(steps, 1):
                args_blob = step.get("args") or {}
                bindings = step.get("arg_bindings") or {}
                args_preview = ", ".join(
                    [f"{k}={v}" for k, v in args_blob.items()] +
                    [f"{k}=<{v}>" for k, v in bindings.items()]
                ) or step.get("intent") or step.get("notes") or ""
                detail = f" — {args_preview}" if args_preview else ""
                lines.append(
                    f"  {i}. {step.get('operation_id', '?')} "
                    f"(plugin={step.get('plugin_id', '?')}){detail}"
                )
            message = "\n".join(lines)
        else:
            message = state.get("clarification_question") or "Please confirm."

        # MCP in-session loop: pause and ask the client to approve. On accept,
        # clear the response and force execution so routing falls through to the
        # builder/executor path within the SAME tool call. On decline/cancel, halt.
        mcp_ctx = _current_mcp_context()
        if mcp_ctx is not None:
            approved = await _elicit(mcp_ctx, message, bool)
            if approved is not None:
                if approved:
                    return {"response": None, "force_execute": True, "gate_decision": "execute"}
                return {"response": {"status": "halted", "message": "Execution cancelled by user."}}

        selected = state.get("selected") or {}
        return {
            "response": {
                "status": "confirmation_needed",
                "message": message,
                "plan": plan,
                "selected_route": {
                    "method": selected.get("method", ""),
                    "path": selected.get("path") or selected.get("path_template", ""),
                    "description": selected.get("description", ""),
                    "confidence": state.get("confidence"),
                } if selected else None,
            },
        }
    return confirm_node

def make_clarify_node(ctx: GraphRuntimeContext):
    """
    Creates a clarification node.
    """
    async def clarify_node(state: DynamicAPIState) -> dict:
        """Asynchronously handle clarification logic by eliciting inputs or questions."""
        question = state.get("clarification_question",
                             "Could you rephrase your request?")

        from core_graph.clarify import build_clarify_questions, format_clarification_hint
        questions = build_clarify_questions(
            candidates=state.get("candidates"),
            llm_questions=state.get("clarify_questions")
        )

        # MCP in-session loop: ask the client to clarify, then re-plan with the
        # answer threaded into the query — all within the same tool call.
        mcp_ctx = _current_mcp_context()
        if mcp_ctx is not None:
            elicit_question = question
            if questions:
                formatted_qs = []
                for q in questions:
                    q_text = f"{q['question']}"
                    if q['options']:
                        opts_text = ", ".join(f"'{opt['label']}'" for opt in q['options'])
                        q_text += f" (Options: {opts_text})"
                    formatted_qs.append(q_text)
                elicit_question = "\n".join(formatted_qs)

            # Use structured enum elicitation when options are available so the
            # frontend can render clickable chips instead of a free-text field.
            has_options = questions and any(q.get("options") for q in questions)
            if has_options:
                try:
                    import typing
                    choices = [opt["label"] for q in questions for opt in (q.get("options") or [])]
                    if choices:
                        LiteralType = typing.Literal.__getitem__(tuple(choices))  # dynamic Literal → enum schema
                        multi_select = any(q.get("multiSelect") for q in questions)
                        if multi_select:
                            try:
                                answer = await _elicit(mcp_ctx, elicit_question, list[LiteralType])
                            except Exception:
                                answer = await _elicit(mcp_ctx, elicit_question, LiteralType)
                        else:
                            answer = await _elicit(mcp_ctx, elicit_question, LiteralType)
                    else:
                        answer = await _elicit(mcp_ctx, elicit_question, str)
                except Exception:
                    answer = await _elicit(mcp_ctx, elicit_question, str)
            else:
                answer = await _elicit(mcp_ctx, elicit_question, str)
            if answer is not None:
                if answer:
                    if isinstance(answer, (list, tuple)):
                        msg_content = ", ".join(str(x) for x in answer if str(x).strip())
                    else:
                        msg_content = str(answer)
                    
                    if msg_content.strip():
                        refined = f"{state.get('user_query', '')}\n\nClarification: {format_clarification_hint(answer)}".strip()
                        return {
                            "user_query": refined,
                            "messages": [HumanMessage(content=msg_content)],
                            "clarification_question": None,
                            "clarify_questions": None,
                            "response": None,
                        }
                return {"response": {"status": "halted", "message": "Clarification cancelled by user."}}

        return {
            "response": {
                "status": "clarification_needed",
                "message": question,
                "questions": questions,
                "candidates": [
                    {
                        "operation_id": c["operation_id"],
                        "method": c.get("method", ""),
                        "path": c.get("path") or c.get("path_template", ""),
                        "description": c["description"],
                        "score": c["score"],
                    }
                    for c in state.get("candidates", [])[:3]
                ],
            },
        }
    return clarify_node
