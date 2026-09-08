import pytest
import re
from typing import Any

from core_graph.graph import (
    _walk_path,
    _resolve_step_value,
    _resolve_arg_bindings,
    _deep_find_key,
    _build_context_params,
    resolve_args_from_context,
    is_recoverable,
)
from core_graph.states import ExecutionStep, RouteCandidate


def test_walk_path_index_support():
    data = {
        "records": [
            {"id": 42, "name": "Andrew"},
            {"id": 43, "name": "Bob"}
        ],
        "meta": {
            "records": [10, 20, 30]
        }
    }
    
    assert _walk_path(data, "records[0].id") == 42
    assert _walk_path(data, "records[1].name") == "Bob"
    assert _walk_path(data, "meta.records[2]") == 30
    assert _walk_path(data, "records[9].id") is None
    assert _walk_path(data, "invalid[0]") is None
    print("[+] test_walk_path_index_support passed")


def test_walk_path_bare_and_trailing():
    data_list = [{"id": 100}]
    assert _walk_path(data_list, "[0].id") == 100
    assert _walk_path(data_list, "[0]") == {"id": 100}
    assert _walk_path(data_list, "[5]") is None
    print("[+] test_walk_path_bare_and_trailing passed")


def test_resolve_step_value_envelopes():
    root = {
        "status": "ok",
        "data": {
            "_meta": {"domain": "records"},
            "data": {
                "records": [{"id": 42}]
            }
        }
    }
    
    assert _resolve_step_value(root, "data.records[0].id") == 42
    assert _resolve_step_value(root, "records[0].id") == 42
    print("[+] test_resolve_step_value_envelopes passed")


def test_deep_find_key_aliases():
    obj = {
        "status": "ok",
        "data": {
            "user_id": 999
        }
    }
    assert _deep_find_key(obj, "id") == 999
    assert _deep_find_key(obj, "userId") == 999
    assert _deep_find_key(obj, "user_id") == 999

    obj_record = {"status": "ok", "data": {"record_id": 999}}
    assert _deep_find_key(obj_record, "record_id") == 999

    obj2 = {
        "status": "ok",
        "data": {
            "someName": "Andrew"
        }
    }
    assert _deep_find_key(obj2, "name") is None
    assert _deep_find_key(obj2, "someName") == "Andrew"
    print("[+] test_deep_find_key_aliases passed")


def test_resolve_args_from_context():
    step = {
        "args": {"message": "hello"},
        "arg_bindings": {"userId": "$steps[0].data.records[0].id"}
    }
    
    step_results = [
        {
            "status": "ok",
            "data": {
                "_meta": {},
                "data": {
                    "records": [{"id": 42}]
                }
            }
        }
    ]
    
    resolved, unresolved = resolve_args_from_context(
        step=step,
        step_results=step_results,
        known_params={},
        route={"parameters": {"properties": {"userId": {}, "message": {}}, "required": ["userId"]}}
    )
    
    assert resolved["userId"] == 42
    assert resolved["message"] == "hello"
    assert not unresolved
    print("[+] test_resolve_args_from_context passed")


def test_resolve_args_from_context_missing_binding_deep_scan():
    step = {
        "args": {"message": "hello"},
        "arg_bindings": {}
    }
    
    step_results = [
        {
            "status": "ok",
            "data": {
                "records": [{"id": 123}]
            }
        }
    ]
    
    resolved, unresolved = resolve_args_from_context(
        step=step,
        step_results=step_results,
        known_params={},
        route={"parameters": {"properties": {"userId": {}, "message": {}}, "required": ["userId"]}}
    )
    
    assert resolved["userId"] == 123
    assert not unresolved
    print("[+] test_resolve_args_from_context_missing_binding_deep_scan passed")


def test_resolve_args_from_context_case_and_defaults():
    step = {"args": {}, "arg_bindings": {}}
    known_params = _build_context_params(
        parameters={"DEFAULT_PROJECT_ID": "proj123", "DEFAULT_WORKSPACE_ID": 456},
    )
    
    route = {
        "parameters": {
            "properties": {
                "projectId": {},
                "workspace_id": {}
            },
            "required": ["projectId"]
        }
    }
    
    resolved, unresolved = resolve_args_from_context(
        step=step,
        step_results=[],
        known_params=known_params,
        route=route
    )
    
    assert resolved["projectId"] == "proj123"
    assert resolved["workspace_id"] == 456
    assert not unresolved
    print("[+] test_resolve_args_from_context_case_and_defaults passed")


def test_is_recoverable_widened():
    assert is_recoverable({"http_status": 400}) is True
    assert is_recoverable({"http_status": 422}) is True
    assert is_recoverable({"http_status": 500}) is False
    assert is_recoverable({"status": "need_input"}) is True
    assert is_recoverable({"status": "error", "message": "TypeError: missing required argument 'userId'"}) is True
    assert is_recoverable({"status": "error", "message": "undefined required parameter"}) is True
    assert is_recoverable({"status": "error", "message": "auth resolution failed"}) is False
    print("[+] test_is_recoverable_widened passed")


@pytest.mark.asyncio
async def test_builder_node_missing_required_fast_path():
    from core_graph.graph import build_dynamic_graph
    from unittest.mock import MagicMock

    # Create a mock LLM
    mock_llm = MagicMock()
    # Build graph
    graph = build_dynamic_graph(llm=mock_llm, route_registry=MagicMock())
    # Extract the builder node
    builder = graph.nodes["builder"]
    if hasattr(builder, "bound"):
        bound = builder.bound
        if hasattr(bound, "afunc") and bound.afunc is not None:
            builder_fn = bound.afunc
        elif hasattr(bound, "func") and bound.func is not None:
            builder_fn = bound.func
        else:
            builder_fn = bound
    elif hasattr(builder, "afunc") and builder.afunc is not None:
        builder_fn = builder.afunc
    elif hasattr(builder, "func") and builder.func is not None:
        builder_fn = builder.func
    else:
        builder_fn = builder

    # Define a fast-path route that has a required parameter
    route = {
        "operation_id": "test_fast_path",
        "plugin_id": "test_plugin",
        "is_fast_path": True,
        "parameters": {
            "required_param": {"required": True, "type": "string"}
        }
    }
    
    # 1. State with missing required parameter
    state = {
        "plan": [{"operation_id": "test_fast_path", "args": {}}],
        "current_step_index": 0,
        "candidates": [route],
        "resolved_args": {},  # missing "required_param"
        "user_query": "test query",
    }
    
    res = await builder_fn(state)
    assert "response" in res
    assert res["response"]["status"] == "need_input"
    assert "required_param" in res["response"]["missing_params"]

    # 2. State with resolved parameter
    state_resolved = {
        "plan": [{"operation_id": "test_fast_path", "args": {"required_param": "value"}}],
        "current_step_index": 0,
        "candidates": [route],
        "resolved_args": {"required_param": "value"},
        "user_query": "test query",
    }
    res_resolved = await builder_fn(state_resolved)
    assert "response" not in res_resolved
    assert res_resolved["payload"] == {"__fast_path__": True}
    assert res_resolved["resolved_args"] == {"required_param": "value"}

    # 3. State with missing required parameter but force_execute=True.
    # force_execute bypasses only the confidence/confirmation gate — it must NOT
    # skip required-param validation, so this still returns need_input.
    state_forced = {
        "plan": [{"operation_id": "test_fast_path", "args": {}}],
        "current_step_index": 0,
        "candidates": [route],
        "resolved_args": {},
        "user_query": "test query",
        "force_execute": True,
    }
    res_forced = await builder_fn(state_forced)
    assert res_forced["response"]["status"] == "need_input"
    assert "required_param" in res_forced["response"]["missing_params"]


def test_resolve_args_from_context_folds_working_memory():
    step = {"args": {}, "arg_bindings": {}}
    route = {"parameters": {"properties": {"record_id": {}}, "required": ["record_id"]}}
    resolved, unresolved = resolve_args_from_context(
        step=step,
        step_results=[],
        known_params={},
        route=route,
        working_memory={"record_id": 77}
    )
    assert resolved["record_id"] == 77 and unresolved == []
    print("[+] test_resolve_args_from_context_folds_working_memory passed")


def test_resolve_args_from_context_working_memory_none_unchanged():
    step = {"args": {}, "arg_bindings": {}}
    route = {"parameters": {"properties": {"record_id": {}}, "required": ["record_id"]}}
    resolved, unresolved = resolve_args_from_context(
        step=step,
        step_results=[],
        known_params={},
        route=route
    )
    assert "record_id" in unresolved and resolved.get("record_id") is None
    print("[+] test_resolve_args_from_context_working_memory_none_unchanged passed")


# ===========================================================================
# GOAP literal-argument capture (consolidated from test_goap_arg_capture.py)
#
# Bug: the GOAP planner compiles steps with ``args={}`` and only derives
# ``arg_bindings`` from preconditions. A literal scalar input the user supplied
# (e.g. the search keyword ``"cat"`` for ``notion-search``) is therefore dropped.
# Fix: the goal-extraction LLM returns ``seed_values`` and a post-pass fills each
# step's ``args`` for required params from ``seed_values`` then ``working_memory``.
# ===========================================================================
import core_graph.graph as g
from core_graph.goap.integrate import parse_seed_values, fill_literal_args, build_seed_world, goap_plan_from_goal


# --- build_seed_world / goap_plan_from_goal seed_values bridge --------------

def test_build_seed_world_includes_seed_values_as_have_facts():
    world = build_seed_world([], {}, {"query": "open cat tunnel mcp project"})
    assert world.has("have:query")


def test_goap_plan_from_goal_single_search_with_seed_values():
    """GOAP can select a single-step search when seed_values satisfy have:query."""
    candidates = [{
        "operation_id": "notion-search",
        "plugin_id": "proxy_notion",
        "method": "CALL",
        "path": "notion-search",
        "description": "Search notion",
        "parameters": {"query": {"required": True, "type": "string"}},
    }]
    goal = ["did:notion-search"]
    seed_values = {"query": "open cat tunnel mcp project"}
    gplan = goap_plan_from_goal(goal, [], {}, candidates, seed_values=seed_values)
    assert gplan is not None
    assert len(gplan.steps) == 1
    assert gplan.steps[0]["operation_id"] == "notion-search"


# --- parse_seed_values ------------------------------------------------------

def test_parse_seed_values_basic():
    assert parse_seed_values({"seed_values": {"query": "cat"}}) == {"query": "cat"}


def test_parse_seed_values_missing_or_bad():
    assert parse_seed_values({}) == {}
    assert parse_seed_values(None) == {}
    assert parse_seed_values({"seed_values": "not-a-dict"}) == {}


# --- fill_literal_args ------------------------------------------------------

def test_fill_literal_args_from_seed_values():
    candidates = [{"operation_id": "notion-search",
                   "parameters": {"query": {"required": True}}}]
    steps = [{"operation_id": "notion-search", "args": {}, "arg_bindings": {}}]
    fill_literal_args(steps, candidates, {"query": "cat"}, {})
    assert steps[0]["args"]["query"] == "cat"


def test_fill_literal_args_from_working_memory():
    candidates = [{"operation_id": "getRecord",
                   "parameters": {"record_id": {"required": True}}}]
    steps = [{"operation_id": "getRecord", "args": {}, "arg_bindings": {}}]
    fill_literal_args(steps, candidates, {}, {"record_id": 123})
    assert steps[0]["args"]["record_id"] == 123


def test_fill_literal_args_seed_values_precedence_over_memory():
    candidates = [{"operation_id": "notion-search",
                   "parameters": {"query": {"required": True}}}]
    steps = [{"operation_id": "notion-search", "args": {}, "arg_bindings": {}}]
    fill_literal_args(steps, candidates, {"query": "cat"}, {"query": "dog"})
    assert steps[0]["args"]["query"] == "cat"


def test_fill_literal_args_skips_bound_params():
    candidates = [{"operation_id": "fetch",
                   "parameters": {"id": {"required": True}}}]
    steps = [{"operation_id": "fetch", "args": {}, "arg_bindings": {"id": "$steps[0].id"}}]
    fill_literal_args(steps, candidates, {"id": "wrong"}, {})
    # A param already produced by a prior step must not be overwritten by a seed.
    assert "id" not in steps[0]["args"]


def test_fill_literal_args_preserves_existing_args():
    candidates = [{"operation_id": "notion-search",
                   "parameters": {"query": {"required": True}}}]
    steps = [{"operation_id": "notion-search", "args": {"query": "explicit"}, "arg_bindings": {}}]
    fill_literal_args(steps, candidates, {"query": "cat"}, {})
    assert steps[0]["args"]["query"] == "explicit"


# --- builder defensive net: fill_missing_from_memory ------------------------

def test_fill_missing_from_memory_exact():
    filled, still = g.fill_missing_from_memory(["record_id"], {}, {"record_id": 7})
    assert filled["record_id"] == 7
    assert still == []


def test_fill_missing_from_memory_alias():
    # recordId (camel) should resolve from record_id in memory via id-aliases.
    filled, still = g.fill_missing_from_memory(["recordId"], {}, {"record_id": 7})
    assert filled.get("recordId") == 7
    assert still == []


def test_fill_missing_from_memory_unresolved():
    filled, still = g.fill_missing_from_memory(["query"], {}, {})
    assert still == ["query"]


# --- user_query deterministic fallback for single free-text leaf params -----

def test_fill_literal_args_user_query_fallback_for_search_param():
    """Fallback fills a leaf single search param (query) from user_query when seed_values and working_memory are empty."""
    candidates = [{"operation_id": "notion-search",
                   "parameters": {"query": {"required": True, "type": "string", "description": "search text"}}}]
    steps = [{"operation_id": "notion-search", "args": {}, "arg_bindings": {}}]
    fill_literal_args(steps, candidates, {}, {}, user_query="open cat tunnel mcp project")
    assert steps[0]["args"]["query"] == "open cat tunnel mcp project"


def test_fill_literal_args_does_not_dump_search_up_imperative():
    """Bare 'search up!' is not a topic — leave query unfilled without history."""
    candidates = [{"operation_id": "web_search",
                   "parameters": {"query": {"required": True, "type": "string"}}}]
    steps = [{"operation_id": "web_search", "args": {}, "arg_bindings": {}}]
    fill_literal_args(steps, candidates, {"query": "search up!"}, {}, user_query="search up!")
    assert "query" not in steps[0].get("args", {})


def test_fill_literal_args_resolves_search_up_from_prior_user_turn():
    """Follow-up 'search up!' binds query to the previous topical user turn."""
    from core_graph.goap.integrate import fill_literal_args
    candidates = [{"operation_id": "web_search",
                   "parameters": {"query": {"required": True, "type": "string"}}}]
    steps = [{"operation_id": "web_search", "args": {}, "arg_bindings": {}}]
    history = [
        {"role": "user", "content": "who is mika misono?"},
        {"role": "assistant", "content": "Mika Misono is a Blue Archive character."},
        {"role": "user", "content": "search up!"},
    ]
    fill_literal_args(
        steps, candidates, {"query": "search up"}, {},
        user_query="search up!",
        history=history,
    )
    assert steps[0]["args"]["query"] == "who is mika misono?"


def test_apply_followup_search_topic_keeps_have_query_for_goap():
    """Replacing the verb (not dropping it) keeps have:query so GOAP can select web_search."""
    from core_graph.goap.integrate import apply_followup_search_topic, build_seed_world

    history = [{"role": "user", "content": "who is mika misono?"}]
    seeds = apply_followup_search_topic({"query": "search up!"}, "search up!", history=history)
    world = build_seed_world([], {}, seeds)
    assert seeds["query"] == "who is mika misono?"
    assert world.has("have:query")


def test_apply_followup_search_topic_keeps_topical_llm_seed():
    """A non-imperative seed the LLM already extracted must not be overwritten."""
    from core_graph.goap.integrate import apply_followup_search_topic

    history = [{"role": "user", "content": "tell me about that trinity student"}]
    seeds = apply_followup_search_topic(
        {"query": "Mika Misono Blue Archive"},
        "search up!",
        history=history,
    )
    assert seeds["query"] == "Mika Misono Blue Archive"


def test_fill_literal_args_user_query_fallback_does_not_apply_to_non_search_param():
    """Fallback does not fire for a non-search param (e.g. record_id) — stays unfilled (builder will emit need_input)."""
    candidates = [{"operation_id": "getRecord",
                   "parameters": {"record_id": {"required": True}}}]
    steps = [{"operation_id": "getRecord", "args": {}, "arg_bindings": {}}]
    fill_literal_args(steps, candidates, {}, {}, user_query="123")
    assert "record_id" not in steps[0].get("args", {})


def test_fill_literal_args_user_query_fallback_does_not_override_bound_or_seed():
    """Fallback does not override a bound param (arg_bindings) or an existing seed value."""
    candidates = [{"operation_id": "notion-search",
                   "parameters": {"query": {"required": True}}}]
    # seed present -> wins
    steps1 = [{"operation_id": "notion-search", "args": {}, "arg_bindings": {}}]
    fill_literal_args(steps1, candidates, {"query": "fromseed"}, {}, user_query="userq")
    assert steps1[0]["args"]["query"] == "fromseed"
    # arg_binding present -> not filled
    steps2 = [{"operation_id": "notion-search", "args": {}, "arg_bindings": {"query": "$steps[0].q"}}]
    fill_literal_args(steps2, candidates, {}, {}, user_query="userq")
    assert "query" not in steps2[0].get("args", {})


def test_fill_literal_args_does_not_dump_user_query_into_prompt():
    """prompt is an instruction field — never last-resort filled from user_query alone."""
    candidates = [{"operation_id": "julescreate_session",
                   "parameters": {"prompt": {"required": True, "type": "string"}}}]
    steps = [{"operation_id": "julescreate_session", "args": {}, "arg_bindings": {}}]
    uq = "Call the tool to create 5 Jules code review agents on branch feat vs main"
    fill_literal_args(steps, candidates, {}, {}, user_query=uq)
    # Unfilled prompt must stay missing (need_input / step_resolver), not raw user_query
    assert steps[0].get("args", {}).get("prompt") in (None, "")


def test_fill_literal_args_enriches_weak_prompt_role_label():
    """Bare role label prompt seeds expand into a brief grounded in user_query."""
    from core_graph.goap.integrate import is_weak_instruction_value

    candidates = [{"operation_id": "julescreate_session",
                   "parameters": {"prompt": {"required": True, "type": "string"}}}]
    steps = [{"operation_id": "julescreate_session", "args": {}, "arg_bindings": {}}]
    uq = (
        "Fire Jules code review agents on branch new-minio-artifact-for-goap-agent "
        "comparing against main"
    )
    fill_literal_args(steps, candidates, {"prompt": "frontend-b"}, {}, user_query=uq)
    prompt = steps[0]["args"]["prompt"]
    assert "frontend-b" in prompt
    assert "new-minio-artifact-for-goap-agent" in prompt
    assert not is_weak_instruction_value(prompt)


def test_is_weak_instruction_value_role_labels():
    from core_graph.goap.integrate import (
        enrich_instruction_value,
        is_weak_instruction_value,
    )

    assert is_weak_instruction_value("frontend-b")
    assert is_weak_instruction_value("docs")
    assert is_weak_instruction_value("backend-a")
    assert not is_weak_instruction_value(
        "Review auth on branch feat vs main.\nCriteria:\n- tenant isolation\n"
    )
    enriched = enrich_instruction_value(
        "docs",
        "Diff review of current branch vs main for documentation gaps",
    )
    assert "docs" in enriched
    assert "Diff review" in enriched
    assert not is_weak_instruction_value(enriched)


# --- _format_candidates / _param_hints enrichment for proxy flat schema -----

def test_format_candidates_and_param_hints_proxy_flat_schema():
    """_param_hints / _format_candidates show required marker (*) + type + (truncated) description for proxy-style flat schema."""
    cand = {
        "operation_id": "notion-search",
        "method": "CALL",
        "path": "notion-search",
        "plugin_id": "proxy_notion",
        "description": "Search notion",
        "score": 0.9,
        "parameters": {  # flat proxy form used by proxy_tool_loader
            "query": {"required": True, "type": "string", "description": "The search query text to use for the open cat tunnel mcp project"}
        }
    }
    hints = g._param_hints(cand)
    assert "query*" in hints[0]
    assert "(string)" in hints[0]
    assert "The search query text to use for the open cat tunnel mcp project" in hints[0] or "The search query text to use" in hints[0]

    formatted = g._format_candidates([cand])
    assert "query*" in formatted
    assert "(string)" in formatted
    # description appears (may be truncated)
    assert "search query text" in formatted.lower()


# --- Proxy-qualified op_id + seed literal survival (regression for notion-style proxies) ---

def test_fill_literal_args_works_for_realistic_proxy_qualified_op_id():
    """Proxy names with hyphens, mixed case and numbers must still receive seed literals."""
    op = "proxy_Notion-AndrewDev-2__notion-search"
    candidates = [{
        "operation_id": op,
        "plugin_id": "proxy_Notion-AndrewDev-2",
        "parameters": {"query": {"required": True, "type": "string"}},
    }]
    steps = [{"operation_id": op, "args": {}, "arg_bindings": {}, "plugin_id": "proxy_Notion-AndrewDev-2", "is_fast_path": True}]
    fill_literal_args(steps, candidates, {"query": "cat"}, {}, user_query="something else")
    assert steps[0]["args"]["query"] == "cat"


def test_proxy_fast_path_args_must_reach_callable_even_after_context_check_builder_simulation():
    """
    Simulate the critical path:
      planner (GOAP + fill) -> context_check (resolve) -> builder (fast-path short-circuit)
      -> executor (or _execute_step) fn(**args)
    Assert the spy wrapper receives the seed value for a proxy-style op.
    """
    from core_graph.goap.integrate import goap_plan_from_goal, goap_steps_to_state, fill_literal_args

    op = "proxy_Notion-AndrewDev-2__notion-search"
    plugin = "proxy_Notion-AndrewDev-2"
    cand = {
        "operation_id": op,
        "plugin_id": plugin,
        "method": "CALL",
        "path": op,
        "description": "Search notion (proxy)",
        "parameters": {"query": {"required": True}},
        "is_fast_path": True,
    }
    candidates = [cand]
    seed_values = {"query": "cat"}

    gplan = goap_plan_from_goal(["did:" + op], ["have:query"], {}, candidates, seed_values=seed_values)
    assert gplan and gplan.steps
    frag = goap_steps_to_state(gplan, candidates)
    fill_literal_args(frag["plan"], candidates, seed_values, {}, user_query="irrelevant")
    assert frag["plan"][0]["args"]["query"] == "cat"

    # Simulate context_check + builder producing the resolved_args that executor will see
    step = frag["plan"][0]
    resolved = dict(step.get("args") or {})
    builder_resolved = dict(resolved)  # what builder would emit for fast-path

    received = {}
    def spy_wrapper(**kwargs):
        received.update(kwargs)
        return {"status": "ok", "data": "fake-notion-results"}

    final_args = dict(builder_resolved)
    base = dict(step.get("args") or {})
    base.update(final_args)
    final_args = base

    result = spy_wrapper(**final_args)

    assert "query" in received and received["query"] == "cat"
    assert result["status"] == "ok"


def test_resume_simulation_keeps_seed_via_defensive_fill_and_merges():
    """After a goal-loop style reset + re-plan, the literal still arrives (via defensive fill + merges)."""
    op = "proxy_Notion-AndrewDev-2__notion-search"
    candidates = [{"operation_id": op, "plugin_id": "proxy_Notion-AndrewDev-2",
                   "parameters": {"query": {"required": True}}}]
    steps = [{"operation_id": op, "args": {}, "arg_bindings": {}, "plugin_id": "proxy_Notion-AndrewDev-2", "is_fast_path": True}]
    fill_literal_args(steps, candidates, {"query": "cat"}, {}, user_query="Call the notion-search tool again, ensuring 'query' is 'cat'")
    assert steps[0]["args"].get("query") == "cat"
