"""
Builder node utilities.
"""
import logging
from core_graph.states import DynamicAPIState
from core_graph.node.context import GraphRuntimeContext
from core_graph.node.helpers import _get_parameters_schema, _resolve_arg_bindings, fill_missing_from_memory, resolve_route_candidate

logger = logging.getLogger("whiskers")

def make_builder_node(ctx: GraphRuntimeContext):
    """
    Creates a builder node.
    """
    async def builder_node(state: DynamicAPIState) -> dict:
        """Construct the HTTP request payload from the builder-generated YAML workflow plan."""
        from core_graph.workflow_yaml import compile_instructions_to_yaml, parse_yaml_to_plan, WorkflowParseError
        from core.llm_config_service import resolve_chat, get_graph_llm

        yaml_workflow = state.get("yaml_workflow")
        workflow_model = state.get("workflow_model")
        workflow_outputs = state.get("workflow_outputs") or {}
        plan = state.get("plan") or []
        parallel_groups = state.get("parallel_groups") or []
        workflow_plan_id = state.get("workflow_plan_id")

        # Persist-once block for pre-populated plan (e.g. from GOAP planner)
        if plan and not yaml_workflow and not workflow_plan_id:
            try:
                from core_graph.workflow_yaml import serialize_plan_to_yaml
                from db_layer.workflow_store import save_workflow_plan
                from core.llm_config_service import resolve_core_chat, resolve_embedding

                yaml_workflow = serialize_plan_to_yaml(
                    plan=plan,
                    parallel_groups=parallel_groups,
                    outputs=workflow_outputs,
                )

                sel = await resolve_core_chat()
                emb_sel = await resolve_embedding()

                llm_provider = sel["provider"] if sel else "unknown"
                llm_model = sel["model"] if sel else "unknown"
                embed_model = f"{emb_sel['provider']}:{emb_sel['model']}" if emb_sel else "unknown"

                if sel:
                    workflow_model = f"{sel['provider']}:{sel['model']}"

                workflow_plan_id = await save_workflow_plan(
                    name=state.get("workflow_name", "goap-plan"),
                    user_query=state.get("user_query", ""),
                    yaml_content=yaml_workflow,
                    compiled_plan=plan,
                    outputs=workflow_outputs,
                    llm_provider=llm_provider,
                    llm_model=llm_model,
                    embed_model=embed_model,
                    status="generated",
                    session_id=state.get("session_id"),
                )
            except Exception as exc:
                logger.warning("Failed persisting GOAP workflow plan to DB: %s", exc)

        # Compile instruction set to Conductor YAML if not yet generated
        out_token_usage = None
        if not yaml_workflow and not plan:
            try:
                from core.llm_config_service import resolve_step_llm_config, resolve_step_llm, resolve_core_chat
                first_step = state["instruction_set"][0] if state.get("instruction_set") else {}
                sel = await resolve_step_llm_config(first_step)
                if not sel:
                    sel = await resolve_core_chat()
                workflow_model = f"{sel['provider']}:{sel['model']}" if sel else "unknown"
                builder_llm = await resolve_step_llm(first_step)

                # LLM call to compile instructions set into YAML workflow
                yaml_workflow, usage = await compile_instructions_to_yaml(
                    user_query=state["user_query"],
                    instruction_set=state["instruction_set"],
                    candidates=state["candidates"],
                    llm=builder_llm,
                    context_params=ctx.context_params,
                )
                from core_graph.node.helpers import fold_token_usage
                model_name = getattr(builder_llm, "model", getattr(builder_llm, "model_name", "unknown"))
                out_token_usage = fold_token_usage(state.get("token_usage"), usage, model_name)
            except Exception as exc:
                logger.exception("Failed compiling instruction set to YAML")
                return {
                    "response": {
                        "status": "error",
                        "message": f"Failed compiling instruction set to YAML: {exc}",
                        "is_yaml_error": True,
                    }
                }

            # Parse and validate the generated YAML
            try:
                plan, parallel_groups, workflow_outputs = parse_yaml_to_plan(
                    yaml_workflow, state["candidates"]
                )
                try:
                    from core.llm_config_service import list_pool
                    from core_graph.goap.integrate import assign_step_models
                    from db_layer.step_model_settings_store import get_step_model_policy
                    _pool = await list_pool(kind="chat")
                    _active = [e for e in _pool if e.get("is_active")]
                    _policy = await get_step_model_policy()
                    assign_step_models(plan, _active, _policy)
                except Exception as exc:
                    logger.warning("builder: per-step model assignment skipped: %s", exc)
            except WorkflowParseError as exc:
                logger.error("Failed parsing YAML to plan: %s", exc)
                res = {
                    "yaml_workflow": yaml_workflow,
                    "workflow_model": workflow_model,
                    "response": {
                        "status": "error",
                        "message": f"Malformed YAML plan: {exc}",
                        "is_yaml_error": True,
                    }
                }
                if out_token_usage is not None:
                    res["token_usage"] = out_token_usage
                return res

            # Save the successfully compiled and validated plan to the database (Phase 3)
            try:
                from db_layer.workflow_store import save_workflow_plan
                from core.llm_config_service import resolve_embedding
                emb_sel = await resolve_embedding()
                embed_model = f"{emb_sel['provider']}:{emb_sel['model']}"

                workflow_plan_id = await save_workflow_plan(
                    name=state.get("workflow_name", "custom-workflow"),
                    user_query=state["user_query"],
                    yaml_content=yaml_workflow,
                    compiled_plan=plan,
                    outputs=workflow_outputs,
                    llm_provider=sel["provider"],
                    llm_model=sel["model"],
                    embed_model=embed_model,
                    status="generated",
                    session_id=state.get("session_id"),
                )
            except Exception as exc:
                logger.warning("Failed persisting workflow plan to DB: %s", exc)

        idx = state.get("current_step_index", 0)
        if idx >= len(plan):
            res = {
                "yaml_workflow": yaml_workflow,
                "workflow_model": workflow_model,
                "workflow_outputs": workflow_outputs,
                "plan": plan,
                "parallel_groups": parallel_groups,
                "workflow_plan_id": workflow_plan_id,
            }
            if out_token_usage is not None:
                res["token_usage"] = out_token_usage
            return res

        step = plan[idx]
        candidates = state.get("candidates") or []
        route = resolve_route_candidate(step["operation_id"], step.get("plugin_id"), candidates, ctx.route_registry)

        if not route:
            res = {
                "yaml_workflow": yaml_workflow,
                "workflow_model": workflow_model,
                "workflow_outputs": workflow_outputs,
                "plan": plan,
                "parallel_groups": parallel_groups,
                "workflow_plan_id": workflow_plan_id,
                "response": {
                    "status": "error",
                    "message": (
                        f"Operation ID '{step['operation_id']}' in step {idx} is not in the current "
                        "candidate routes for this turn. (Common with proxy tools whose exact qualified "
                        "name like 'proxy_Notion-...__notion-search' was not returned by the latest "
                        "pgvector embedder, e.g. on goal-loop resume with a narrow hint query.)"
                    ),
                }
            }
            if out_token_usage is not None:
                res["token_usage"] = out_token_usage
            return res

        # context_check (+ step_resolver) is the single authoritative arg resolver.
        # Builder trusts resolved_args; the check below is a thin retry-path safety net.
        args = dict(state.get("resolved_args") or {})

        # Check for missing parameters against the candidate route schema
        route_schema = _get_parameters_schema(route)
        missing = []
        for p_name, p_schema in route_schema.items():
            if p_schema.get("required") and (args.get(p_name) is None or args.get(p_name) == ""):
                # Attempt to inject environment default for this missing parameter
                env_val = ctx.context_params.get(p_name)
                if env_val:
                    args[p_name] = env_val
                else:
                    missing.append(p_name)

        if missing:
            from core_graph.clarify import build_clarify_questions
            questions = build_clarify_questions(missing_params=missing)
            res = {
                "yaml_workflow": yaml_workflow,
                "workflow_model": workflow_model,
                "workflow_outputs": workflow_outputs,
                "plan": plan,
                "parallel_groups": parallel_groups,
                "workflow_plan_id": workflow_plan_id,
                "response": {
                    "status": "need_input",
                    "missing_params": missing,
                    "message": f"Please provide: {', '.join(missing)}",
                    "questions": questions,
                },
            }
            if out_token_usage is not None:
                res["token_usage"] = out_token_usage
            return res

        # Fast-path short-circuit
        if route.get("is_fast_path") and ctx.route_registry is not None:
            res = {
                "yaml_workflow": yaml_workflow,
                "workflow_model": workflow_model,
                "workflow_outputs": workflow_outputs,
                "plan": plan,
                "parallel_groups": parallel_groups,
                "workflow_plan_id": workflow_plan_id,
                "selected": route,
                "payload": {"__fast_path__": True},
                "resolved_args": args,
            }
            if out_token_usage is not None:
                res["token_usage"] = out_token_usage
            return res

        # Classify args into path, query, and body parameters
        path = route.get("path_template") or route.get("path") or ""
        path_params = {}
        query_params = {}
        body = {}
        for p_name, p_val in args.items():
            if f"{{{p_name}}}" in path:
                path_params[p_name] = p_val
            elif p_name in route_schema:
                method = route.get("method", "GET").upper()
                if method in ("POST", "PUT", "PATCH"):
                    body[p_name] = p_val
                else:
                    query_params[p_name] = p_val
            else:
                query_params[p_name] = p_val

        # Replace path parameters in the templates
        for p_name, p_val in path_params.items():
            path = path.replace(f"{{{p_name}}}", str(p_val))

        url_base = ctx.api_url or ""
        full_url = f"{url_base}{path}" if path.startswith("/") else path

        payload = {
            "url": full_url,
            "method": route.get("method", "GET"),
            "operation_id": route.get("operation_id", ""),
            "plugin_id": route.get("plugin_id", ""),
            "path_params": path_params,
            "query_params": query_params,
            "body": body if body else None,
        }

        res = {
            "payload": payload,
            "resolved_args": args,
            "yaml_workflow": yaml_workflow,
            "workflow_plan_id": workflow_plan_id,
        }
        if out_token_usage is not None:
            res["token_usage"] = out_token_usage
        return res
    return builder_node
