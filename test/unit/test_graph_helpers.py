import pytest
import core_graph.node.helpers.gating
core_graph.node.helpers.gating.LONG_CHAIN_THRESHOLD = 3
core_graph.node.helpers.gating.CONFIDENCE_EXECUTE_THRESHOLD = 0.70
core_graph.node.helpers.gating.CONFIDENCE_CONFIRM_THRESHOLD = 0.50

from core_graph.graph import compute_gate, _parse_json_response, _resolve_arg_bindings

def test_high_confidence_execute():
    assert compute_gate(0.85, 0.90, 1)[1] == "execute"

def test_medium_confidence_confirm():
    assert compute_gate(0.60, 0.55, 1)[1] == "confirm"

def test_low_confidence_clarify():
    assert compute_gate(0.30, 0.40, 1)[1] == "clarify"

def test_long_chain_forces_confirm():
    assert compute_gate(0.95, 0.95, 4)[1] == "confirm"

def test_llm_score_none_uses_pgvector():
    # If llm_score is None, p=0.8. score = 0.6*0.8 + 0.4*0.8 = 0.8
    # threshold for execute is 0.8, so it should be execute
    assert compute_gate(0.81, None, 1)[1] == "execute"


def test_parse_json_response_plain_json():
    assert _parse_json_response('{"a": 1}') == {"a": 1}

def test_parse_json_response_markdown_fenced():
    assert _parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}

def test_parse_json_response_embedded_in_text():
    assert _parse_json_response('Here is the plan: {"steps": []} have fun') == {"steps": []}

def test_parse_json_response_list_input():
    assert _parse_json_response(['{"a": ', '1}']) == {"a": 1}

def test_parse_json_response_returns_none_on_garbage():
    assert _parse_json_response('Hello world') is None

def test_resolve_arg_bindings_literal_passthrough():
    args = {"id": 42}
    res = _resolve_arg_bindings(args, [{"id": 1}])
    assert res == {"id": 42}

def test_resolve_arg_bindings_step_reference():
    args = {"id": "$steps[0].id"}
    res = _resolve_arg_bindings(args, [{"id": 42}])
    assert res == {"id": 42}

def test_resolve_arg_bindings_envelope_unwrap():
    args = {"id": "$steps[0].id"}
    res = _resolve_arg_bindings(args, [{"data": {"id": 99}}])
    assert res == {"id": 99}

def test_resolve_arg_bindings_out_of_range():
    args = {"id": "$steps[5].id"}
    res = _resolve_arg_bindings(args, [])
    assert "id" not in res


def test_retry_router_replan():
    from core_graph.graph import retry_router
    state = {"response": {"status": "replan"}, "retry_count": 0}
    assert retry_router(state) == "replan"


def test_retry_router_recoverable_under_budget():
    from core_graph.graph import retry_router
    # A 400 bad request error is recoverable
    state = {"response": {"status": "error", "http_status": 400}, "retry_count": 0}
    assert retry_router(state) == "retry"


def test_retry_router_recoverable_over_budget():
    from core_graph.graph import retry_router
    # Retries exhausted
    state = {"response": {"status": "error", "http_status": 400}, "retry_count": 1}
    assert retry_router(state) == "halt"


def test_retry_router_need_input_halts():
    from core_graph.graph import retry_router
    # Missing required parameter needs human input; never retry or replan
    state = {"response": {"status": "need_input"}, "retry_count": 0}
    assert retry_router(state) == "halt"


@pytest.mark.asyncio
async def test_planner_node_clears_response():
    from unittest.mock import AsyncMock, MagicMock
    from core_graph.graph import build_dynamic_graph
    
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = MagicMock(content='{"instructions": [{"intent": "test", "operation_id": "test_op"}], "name": "test-workflow", "confidence": 0.9}')
    
    graph = build_dynamic_graph(mock_llm)
    planner_node = graph.nodes["planner"].bound
    
    state = {
        "user_query": "test query",
        "candidates": [{"operation_id": "test_op", "score": 0.95, "plugin_id": "test_plugin", "description": "test"}],
        "response": {"status": "replan"},
        "current_step_index": 0,
        "step_results": [],
        "messages": []
    }
    
    result = await planner_node.ainvoke(state)
    assert result.get("response") is None


def test_retry_router_error_with_goal():
    from core_graph.graph import retry_router
    # Terminal error with a goal active routes to recover
    state = {"response": {"status": "error"}, "retry_count": 0, "goal": "Find a record"}
    assert retry_router(state) == "recover"


def test_retry_router_error_no_goal():
    from core_graph.graph import retry_router
    # Terminal error without a goal active halts
    state = {"response": {"status": "error"}, "retry_count": 0}
    assert retry_router(state) == "halt"


@pytest.mark.asyncio
async def test_validator_node_terminal_error_with_goal():
    from unittest.mock import AsyncMock
    from core_graph.graph import build_dynamic_graph
    
    mock_llm = AsyncMock()
    graph = build_dynamic_graph(mock_llm)
    validator_node = graph.nodes["validator"].bound
    
    # Non-recoverable error with goal
    state = {
        "response": {"status": "error", "message": "Critical connection failure"},
        "retry_count": 0,
        "goal": "Send report",
        "step_results": [{"status": "ok", "data": "Step 1 result"}]
    }
    
    result = await validator_node.ainvoke(state)
    
    assert result.get("response") is not None
    assert result["response"]["status"] == "error"
    assert "Execution failed" in result["response"]["message"]
    
    assert result.get("step_results") is not None
    assert len(result["step_results"]) == 2
    assert result["step_results"][0] == {"status": "ok", "data": "Step 1 result"}
    assert result["step_results"][1] == result["response"]


@pytest.mark.asyncio
async def test_validator_node_terminal_error_no_goal():
    from unittest.mock import AsyncMock
    from core_graph.graph import build_dynamic_graph
    
    mock_llm = AsyncMock()
    graph = build_dynamic_graph(mock_llm)
    validator_node = graph.nodes["validator"].bound
    
    # Non-recoverable error without goal
    state = {
        "response": {"status": "error", "message": "Critical connection failure"},
        "retry_count": 0,
        "step_results": [{"status": "ok", "data": "Step 1 result"}]
    }
    
    result = await validator_node.ainvoke(state)
    
    assert result.get("response") is not None
    assert result["response"]["status"] == "error"
    # Without goal, step_results should NOT be returned/appended
    assert "step_results" not in result


@pytest.mark.asyncio
async def test_builder_node_force_execute_still_requires_params():
    from unittest.mock import AsyncMock
    from core_graph.graph import build_dynamic_graph
    
    mock_llm = AsyncMock()
    graph = build_dynamic_graph(mock_llm)
    builder_node = graph.nodes["builder"].bound
    
    state = {
        "force_execute": True,
        "current_step_index": 0,
        "plan": [{"operation_id": "op_x", "args": {}, "arg_bindings": {}}],
        "candidates": [{"operation_id": "op_x", "method": "GET", "path": "/x",
            "parameters": {"id": {"required": True}}, "is_fast_path": False,
            "plugin_id": "p", "description": "d", "score": 0.9}],
        "yaml_workflow": "wf", "step_results": [], "resolved_args": {},
    }
    
    result = await builder_node.ainvoke(state)
    
    assert result.get("response") is not None
    assert result["response"]["status"] == "need_input"
    assert "id" in result["response"]["missing_params"]


@pytest.mark.asyncio
async def test_goal_check_done_sets_terminal_response_when_missing():
    from unittest.mock import AsyncMock
    from core_graph.graph import build_dynamic_graph

    mock_llm = AsyncMock()
    graph = build_dynamic_graph(mock_llm)
    goal_check_node = graph.nodes["goap_goal"].bound

    state = {
        "goal": "g",
        "iterations": 99,
        "max_iterations": 5,
        "response": None,
        "step_results": [],
        "summary": None,
    }

    result = await goal_check_node.ainvoke(state)
    assert result["goal_loop_decision"] == "done"
    assert result["response"] is not None
    assert result["response"]["status"] == "incomplete"
    assert "Goal not completed within 100 iteration(s)." in result["response"]["message"]
    assert result["iterations"] == 100
    assert result["response"]["working_memory"] == {}


@pytest.mark.asyncio
async def test_goal_check_done_preserves_existing_response():
    from unittest.mock import AsyncMock
    from core_graph.graph import build_dynamic_graph

    mock_llm = AsyncMock()
    graph = build_dynamic_graph(mock_llm)
    goal_check_node = graph.nodes["goap_goal"].bound

    state = {
        "goal": "g",
        "iterations": 99,
        "max_iterations": 5,
        "response": {"status": "error", "message": "boom"},
        "step_results": [],
        "summary": None,
    }

    result = await goal_check_node.ainvoke(state)
    assert result["goal_loop_decision"] == "done"
    assert result["response"] == {"status": "error", "message": "boom"}


@pytest.mark.asyncio
async def test_goal_check_done_sets_ok_response_when_summary_exists():
    from unittest.mock import AsyncMock
    from core_graph.graph import build_dynamic_graph

    mock_llm = AsyncMock()
    graph = build_dynamic_graph(mock_llm)
    goal_check_node = graph.nodes["goap_goal"].bound

    state = {
        "goal": "g",
        "iterations": 99,
        "max_iterations": 5,
        "response": None,
        "step_results": [],
        "summary": "Done all tasks",
    }

    result = await goal_check_node.ainvoke(state)
    assert result["goal_loop_decision"] == "done"
    assert result["response"] is not None
    assert result["response"]["status"] == "ok"
    assert result["response"]["message"] == "Done all tasks"
    assert result["response"]["summary"] == "Done all tasks"


def test_resolve_step_value_results_envelope():
    from core_graph.node.helpers import _resolve_step_value
    # Structured response with results list
    res = {
        "results": [
            {"id": "p-123", "url": "https://notion.so/p-123"}
        ]
    }
    # resolve $steps[0].url (path is "url", root is the response)
    val = _resolve_step_value(res, "url")
    assert val == "https://notion.so/p-123"


@pytest.mark.asyncio
async def test_goal_check_no_progress_guard():
    from unittest.mock import AsyncMock
    from core_graph.graph import build_dynamic_graph

    mock_llm = AsyncMock()
    graph = build_dynamic_graph(mock_llm)
    goal_check_node = graph.nodes["goap_goal"].bound

    # State has a repeated terminal failure and working memory gained nothing
    state = {
        "goal": "g",
        "iterations": 1,
        "max_iterations": 5,
        "response": {"status": "error", "message": "Failed to connect to database"},
        "last_failure": {"detail": "Failed to connect to database"},
        "repeat_failure_count": 1,
        "step_results": [],
        "working_memory": {"existing_id": 42},
    }

    result = await goal_check_node.ainvoke(state)
    assert result["goal_loop_decision"] == "done"
    assert result["response"]["status"] == "error"
    assert "Failed to connect to database" in result["response"]["message"]






