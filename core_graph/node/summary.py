"""
Summary node utilities.
"""
import json
import logging
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from core_graph.states import DynamicAPIState
from core_graph.prompts import SUMMARY_PROMPT
from core_graph.prompts.goal_check_prompt import GOAL_CHECK_PROMPT
from core_graph.goap.goal_loop import (
    fold_working_memory,
    parse_goal_check,
    decide_goal_loop,
    format_research_notes,
    DEFAULT_MAX_ITERATIONS,
)
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.helpers import format_replan_context, _parse_json_response

logger = logging.getLogger("whiskers")

# Prose longer than this that still looks like JSON is treated as a dump, not a summary.
_JSON_BLOB_SOFT_LEN = 400


def _looks_like_json_blob(text: str) -> bool:
    """True when text is a JSON dump (or truncated JSON) rather than chat prose.

    Used so summary_node never parks raw step-result / envelope JSON on
    ``response.summary`` / ``response.message`` for the playground chat bubble.
    """
    t = (text or "").strip()
    if not t:
        return False
    if not (t.startswith("{") or t.startswith("[")):
        return False
    # Any parseable JSON structure is not natural-language chat prose.
    try:
        json.loads(t)
        return True
    except (json.JSONDecodeError, TypeError, ValueError):
        # Truncated JSON still starts with { / [ — treat as blob once moderately long.
        return len(t) > 80 or len(t) > _JSON_BLOB_SOFT_LEN


def _looks_like_layout(obj: object) -> bool:
    """True when obj looks like a portfolio layout (blocks list is the core signal).

    Prefer version==1 + blocks; also accept dicts that only have blocks (older
    fixtures / partial envelopes) so fold-into-carry stays robust. Frontend Zod
    is the strict gate.
    """
    if not isinstance(obj, dict):
        return False
    if not isinstance(obj.get("blocks"), list):
        return False
    # Reject empty false-positives that are clearly tool envelopes.
    if "status" in obj and "layout" not in obj and obj.get("version") is None:
        return False
    version = obj.get("version")
    return version is None or version == 1


def _coerce_layout_candidate(obj: object) -> dict | None:
    """Return a layout dict from raw tool payload forms (dict or JSON string)."""
    if isinstance(obj, str):
        text = obj.strip()
        if not text or text[0] not in "{[":
            return None
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    if not isinstance(obj, dict):
        return None
    # Explicit layout key wins (emit_layout / design_layout tool envelope).
    nested = obj.get("layout")
    if isinstance(nested, dict) and isinstance(nested.get("blocks"), list):
        return nested
    if _looks_like_layout(obj):
        return obj
    # MCP / executor wraps: {"data": {...}} / {"response": {...}}.
    for key in ("data", "response", "result"):
        sub = obj.get(key)
        found = _coerce_layout_candidate(sub)
        if found is not None:
            return found
    return None


def _extract_layout_from_step_results(step_results: list) -> dict | None:
    """Return the last layout dict found in step_results (emit_layout / design_layout).

    Handles common executor envelopes:
    - ``{"status":"ok","layout":{...}}`` (fast-path MCP tool return parked on response)
    - ``{"response": {"layout": {...}}}`` / ``{"data": {"layout": ...}}``
    - list envelopes under ``data`` / ``results``
    - JSON-string payloads (some adapters stringify tool results)
    - bare layout objects ``{version:1, meta, blocks}``
    """
    layout = None
    for entry in step_results or []:
        found = _coerce_layout_candidate(entry)
        if found is not None:
            layout = found
            continue
        if not isinstance(entry, dict):
            continue
        # Nested list envelopes the recursive coerce may miss (only walks dicts).
        for list_key in ("data", "results"):
            bag = entry.get(list_key)
            if not isinstance(bag, list):
                continue
            for item in bag:
                nested = _coerce_layout_candidate(item)
                if nested is not None:
                    layout = nested
    return layout


def _specialist_authoritative_summary(pre: dict) -> str | None:
    """Build chat prose from a specialist pipeline envelope (authoritative).

    Specialist paths stamp ``response`` with a good summary (and optional
    domain payloads like layout/short_id) before summary_node; the LLM must
    not rewrite empty step_results into "No step results were provided…".
    """
    if not isinstance(pre, dict) or not pre.get("specialist"):
        return None

    short_id = pre.get("short_id") or pre.get("portfolio_job_id")
    layout = pre.get("layout") if isinstance(pre.get("layout"), dict) else None
    n_blocks = len((layout or {}).get("blocks") or []) if layout else 0
    pre_summary = (pre.get("summary") or pre.get("message") or "").strip()
    answer = pre.get("answer_markdown")
    if isinstance(answer, str) and answer.strip():
        pre_summary = answer.strip()
    weak = (
        not pre_summary
        or _looks_like_json_blob(pre_summary)
        or "no step results" in pre_summary.lower()
        or pre_summary.lower().startswith("the workflow to")
        or pre_summary.lower().startswith("flow '")
    )

    # Domain-specific portfolio bake / layout (optional payload keys).
    if short_id:
        if weak:
            return (
                f"Baked job portfolio short_id={short_id}"
                + (f" ({n_blocks} blocks)" if n_blocks else "")
                + f". Open with ?j={short_id}."
            )
        return pre_summary

    if layout is not None:
        if weak:
            gclass = pre.get("goal_class") or pre.get("specialist_domain") or "specialist"
            return f"Composed layout ({n_blocks} blocks, mode={gclass})."
        return pre_summary

    # Generic specialist: prefer stamped summary; fallback from output.
    if pre_summary and not weak:
        return pre_summary

    output = pre.get("output")
    if isinstance(output, dict):
        for key in ("summary", "message", "content", "answer", "text"):
            val = output.get(key)
            if isinstance(val, str) and val.strip() and not _looks_like_json_blob(val):
                return val.strip()

    if pre_summary:
        return pre_summary
    return None


def make_summary_node(ctx: GraphRuntimeContext):
    """
    Creates the final synthesis summary node (runs once on the goal-loop done path).
    """
    async def summary_node(state: DynamicAPIState) -> dict:
        """Generate a natural-language summary of the completed execution.

        Final-only: per-round accumulation lives in ``round_summary`` /
        ``research_notes``. This node synthesizes against the original question
        plus the full cross-round evidence log.
        """
        step_results = state.get("step_results") or []
        # `user_query` gets rewritten to a focused sub-directive on every goal-loop
        # replan (see goap_goal.py continue paths), so by the final round it no longer
        # reflects what the user actually asked. `original_query` is the stable,
        # never-mutated question for the whole turn (stamped once by
        # reset_turn_fragment); `goal` is the planner's intent as a fallback for turns
        # that predate/bypass that field.
        question = state.get("original_query") or state.get("goal") or state.get("user_query") or ""
        working_memory = state.get("working_memory") or {}
        artifacts = list(state.get("artifacts") or [])

        # Second-chance offload if round_summary missed; rebinds response.data.
        try:
            from core_graph.node.artifact_offload import offload_step_results_for_state

            step_results, artifacts, slim_resp = await offload_step_results_for_state(
                {**state, "artifacts": artifacts},
                step_results=step_results,
            )
            if slim_resp is not None:
                state = {
                    **state,
                    "response": slim_resp,
                    "step_results": step_results,
                    "artifacts": artifacts,
                }
        except Exception as exc:
            logger.warning("summary_node artifact offload skipped: %s", exc, exc_info=True)

        summary_text = ""
        content_val = ""
        carry_val = {}

        # Specialist stack already produced an authoritative envelope — keep it.
        pre_response = state.get("response") if isinstance(state.get("response"), dict) else {}
        specialist_summary = _specialist_authoritative_summary(pre_response)
        if specialist_summary:
            summary_text = specialist_summary
            content_val = pre_response.get("content") or ""
            carry_val = (
                dict(pre_response["carry"])
                if isinstance(pre_response.get("carry"), dict)
                else {}
            )
            layout_pre = pre_response.get("layout")
            if isinstance(layout_pre, dict) and isinstance(layout_pre.get("blocks"), list):
                carry_val = {**carry_val, "layout": layout_pre}
        else:
            step_results_text = json.dumps(step_results, indent=2, default=str)[:16000]
            prior_notes = format_research_notes(state.get("research_notes"))
            prior_block = f"Prior round notes:\n{prior_notes}\n\n" if prior_notes else ""
            artifacts_block = ""
            if artifacts:
                lines = []
                for a in artifacts:
                    if not isinstance(a, dict):
                        continue
                    sid = a.get("short_id") or "?"
                    kind = a.get("kind") or "blob"
                    nbytes = a.get("bytes") or 0
                    path = a.get("path") or a.get("source_path") or ""
                    lines.append(f"- {sid}  kind={kind}  {nbytes} bytes  path={path}")
                if lines:
                    artifacts_block = (
                        "Artifacts (inline previews are preferred; call fetch_artifact "
                        "ONLY if a full body is needed; console "
                        "/api/artifacts/session_gated/{id}):\n"
                        + "\n".join(lines)
                        + "\n\n"
                    )
            messages = [
                SystemMessage(content=SUMMARY_PROMPT),
                HumanMessage(
                    content=f"User request: {question}\n\n"
                    f"Known facts (working memory): {working_memory}\n\n"
                    f"{prior_block}"
                    f"{artifacts_block}"
                    f"Step results:\n{step_results_text}"
                ),
            ]

            try:
                from core_graph.model_roles.ladder import run_role_ladder

                summary_res = await run_role_ladder(
                    "summary",
                    state=state,
                    node="summary",
                    attempt=lambda llm: llm.ainvoke(messages),
                    fallback_llm=ctx.llm,
                    token_usage=state.get("token_usage"),
                )
                response = summary_res.value
                if response is None:
                    raise RuntimeError("summary ladder exhausted with no usable response")
                state["token_usage"] = summary_res.token_usage
                state["model_audit"] = [summary_res.audit_entry("summary")]

                content = response.content
                if isinstance(content, list):
                    content = "".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                raw_text = str(content).strip()

                parsed = _parse_json_response(raw_text)
                if isinstance(parsed, dict) and "summary" in parsed:
                    summary_text = (parsed.get("summary") or "").strip()
                    content_val = parsed.get("content") or ""
                    carry_val = parsed.get("carry") or {}
                    if not isinstance(carry_val, dict):
                        carry_val = {}
                    # Empty or JSON-dumped "summary" is not chat prose — use fallback.
                    if not summary_text or _looks_like_json_blob(summary_text):
                        steps_n = len(step_results)
                        summary_text = (
                            f"Completed {steps_n} step{'s' if steps_n != 1 else ''} successfully."
                        )
                elif raw_text and not _looks_like_json_blob(raw_text):
                    # Plain NL prose from the model is fine; never echo JSON dumps.
                    summary_text = raw_text
                    content_val = ""
                    carry_val = {}
                else:
                    steps_n = len(step_results)
                    summary_text = (
                        f"Completed {steps_n} step{'s' if steps_n != 1 else ''} successfully."
                    )
                    content_val = ""
                    carry_val = {}
            except Exception as exc:
                logger.warning("summary_node LLM/parse call failed: %s", exc)
                steps_n = len(step_results)
                summary_text = f"Completed {steps_n} step{'s' if steps_n != 1 else ''} successfully."
                content_val = ""
                carry_val = {}

        # Stable frontend path: fold last emit_layout / design_layout into carry.
        # Also mirror layout at response.layout so clients that don't dig into
        # carry still receive the composed UI payload.
        layout = _extract_layout_from_step_results(step_results)
        if layout is None and isinstance(pre_response, dict):
            layout = _coerce_layout_candidate(pre_response.get("layout"))
        if layout is None:
            # Fallback: prior iteration may have parked layout on working_memory.
            wm = state.get("working_memory") or {}
            if isinstance(wm, dict):
                layout = _coerce_layout_candidate(wm.get("layout"))
        if layout is not None:
            carry_val = {**carry_val, "layout": layout}

        current_response = state.get("response") or {}
        # Prefer slim step_results for envelope data (never re-inject fat bodies).
        if (
            isinstance(current_response, dict)
            and current_response.get("steps_executed") is not None
        ):
            current_response = {
                **current_response,
                "data": (
                    step_results
                    if len(step_results) > 1
                    else (step_results[0] if step_results else {})
                ),
            }
        new_response = {
            **current_response,
            "summary": summary_text,
            "message": summary_text,
            "content": content_val,
            "carry": carry_val,
        }
        if layout is not None:
            new_response["layout"] = layout
        # Small MinIO offload refs (short_id/kind/bytes) — sacred on the envelope.
        if artifacts:
            from core_graph.node.artifact_offload import _public_artifact_refs

            new_response["artifacts"] = _public_artifact_refs(artifacts)
        
        old_working_memory = state.get("working_memory") or {}
        new_working_memory = {**old_working_memory, **carry_val}
        
        token_usage_stats = state.get("token_usage") or {}
        
        execution_id = state.get("execution_id")
        if execution_id:
            try:
                from db_layer.execution_store import finalize_execution
                await finalize_execution(
                    exec_id=execution_id,
                    step_results=step_results,
                    working_memory=new_working_memory,
                    summary=summary_text,
                    content=content_val,
                    carry=carry_val,
                    status="done",
                    input_tokens=token_usage_stats.get("input_tokens", 0),
                    output_tokens=token_usage_stats.get("output_tokens", 0),
                    total_tokens=token_usage_stats.get("total_tokens", 0),
                    model_usage=token_usage_stats.get("model_usage", {}),
                )
            except Exception as exc:
                logger.warning("Failed to finalize workflow execution in summary_node: %s", exc)

        # Durable plan recipe for cross-run harness RAG (op chain only, no PII args).
        try:
            from core_graph.harness import record_success_recipe

            await record_success_recipe(
                {
                    **state,
                    "summary": summary_text,
                    "plan": state.get("plan") or [],
                    "step_results": step_results,
                    "user_query": question,          # embed the real request, not the last sub-directive
                    "execution_id": execution_id,
                }
            )
        except Exception as exc:
            logger.warning("summary_node harness recipe write failed: %s", exc)

        result = {
            "summary": summary_text,
            "working_memory": new_working_memory,
            "response": new_response,
            "step_results": step_results,
            "artifacts": artifacts,
            "token_usage": token_usage_stats,
            "model_audit": state.get("model_audit") or [],
        }
        if summary_text:
            result["messages"] = [AIMessage(content=summary_text)]
        return result
    return summary_node

def make_goal_check_node(ctx: GraphRuntimeContext):
    """
    Creates a goal check node.
    """
    async def goal_check_node(state: DynamicAPIState) -> dict:
        """Evaluate if the overarching goal has been completed, or if more planning steps are required."""
        iterations = state.get("iterations", 0) + 1
        working_memory = fold_working_memory(state.get("working_memory") or {}, state.get("step_results") or [])
        last_summary = state.get("summary") or state.get("last_summary")
        goal = state.get("goal")
        max_iter = state.get("max_iterations") or DEFAULT_MAX_ITERATIONS

        # Helper to build/preserve terminal response
        def get_terminal_response() -> dict:
            """Construct a fallback terminal response dict from the current state."""
            existing = state.get("response")
            if existing:
                return existing
            if last_summary:
                return {
                    "status": "ok",
                    "message": last_summary,
                    "summary": last_summary,
                    "working_memory": working_memory,
                }
            return {
                "status": "incomplete",
                "message": f"Goal not completed within {iterations} iteration(s).",
                "working_memory": working_memory,
            }

        # Check for repeated terminal failure and no progress
        gained_nothing = True
        old_mem = state.get("working_memory") or {}
        for k, v in working_memory.items():
            if old_mem.get(k) != v:
                gained_nothing = False
                break

        current_failure = None
        resp = state.get("response")
        if isinstance(resp, dict) and resp.get("status") in ("error", "need_input"):
            current_failure = resp.get("message") or resp.get("status")

        last_fail = state.get("last_failure")
        last_fail_str = None
        if isinstance(last_fail, dict):
            last_fail_str = last_fail.get("detail") or last_fail.get("message")
        elif isinstance(last_fail, str):
            last_fail_str = last_fail

        is_repeated_failure = False
        if current_failure and last_fail_str:
            if current_failure == last_fail_str:
                is_repeated_failure = True

        repeat_failure_count = state.get("repeat_failure_count", 0)
        if is_repeated_failure and gained_nothing:
            repeat_failure_count += 1
        else:
            repeat_failure_count = 0

        if repeat_failure_count >= 2:
            logger.warning("Goal loop detected repeated terminal failure with no progress. Stopping early.")
            return {
                "iterations": iterations,
                "working_memory": working_memory,
                "last_summary": last_summary,
                "response": get_terminal_response(),
                "goal_loop_decision": "done",
                "repeat_failure_count": repeat_failure_count,
            }

        # Fast exit: if not goal or iterations >= max_iter:
        if not goal or iterations >= max_iter:
            return {
                "iterations": iterations,
                "working_memory": working_memory,
                "last_summary": last_summary,
                "response": get_terminal_response(),
                "goal_loop_decision": "done",
                "repeat_failure_count": repeat_failure_count,
            }

        # Else LLM check (wrap in try/except; on exception → decision "done"):
        try:
            rc = format_replan_context(state.get("replan_context")) if state.get("replan_context") else ""
            msgs = [
                SystemMessage(content=GOAL_CHECK_PROMPT),
                HumanMessage(
                    content=f"Goal: {goal}\n\n"
                    f"Working memory: {working_memory}\n\n"
                    f"{rc}\n\n"
                    f"Step results:\n{json.dumps(state.get('step_results') or [], default=str)[:4000]}"
                ),
            ]
            response = await ctx.llm.ainvoke(msgs)
            
            from utils.telemetry import extract_token_usage
            from core_graph.node.helpers import fold_token_usage
            usage = extract_token_usage(response)
            model_name = getattr(ctx.llm, "model", getattr(ctx.llm, "model_name", "unknown"))
            new_token_usage = fold_token_usage(state.get("token_usage"), usage, model_name)
            
            parsed = _parse_json_response(response.content)
            done, reason, next_hint = parse_goal_check(parsed)
            decision = decide_goal_loop(goal, iterations, max_iter, done)
            if decision == "continue":
                last_plan_op_ids = [s.get("operation_id") for s in state.get("plan") or [] if s.get("operation_id")]
                return {
                    "iterations": iterations,
                    "working_memory": working_memory,
                    "last_summary": last_summary,
                    "user_query": next_hint or goal,          # drive the next sub-plan
                    "plan": [],
                    "current_step_index": 0,
                    "step_results": [],
                    "last_results": state.get("step_results") or [],
                    "yaml_workflow": "",
                    "instruction_set": [],
                    "selected": None,
                    "response": None,                          # INVARIANT: clear before re-plan
                    "goal_loop_decision": "continue",
                    # Forward the prior seed_values (and the ones we may have carried
                    # from the GOAP planner) so the immediate re-embed/re-plan has
                    # the literals even before the goal LLM re-extracts them from the hint.
                    "seed_values": state.get("seed_values"),
                    "last_failure": current_failure or state.get("last_failure"),
                    "execution_id": None,
                    "repeat_failure_count": repeat_failure_count,
                    "last_plan_op_ids": last_plan_op_ids,
                    "token_usage": new_token_usage,
                }
        except Exception as exc:
            logger.error("Error during goal evaluation check: %s", exc)
            new_token_usage = state.get("token_usage")

        return {
            "iterations": iterations,
            "working_memory": working_memory,
            "last_summary": last_summary,
            "response": get_terminal_response(),
            "goal_loop_decision": "done",
            "repeat_failure_count": repeat_failure_count,
            "token_usage": new_token_usage if 'new_token_usage' in locals() else state.get("token_usage"),
        }
    return goal_check_node
