import pytest
from unittest.mock import AsyncMock, MagicMock
import requests

from core_graph.graph import build_dynamic_graph
from utils.api_utils import _extract_api_error, api_error_dict

def test_extract_api_error_empty_body():
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 404
    resp.reason = "Not Found"
    resp.json.side_effect = ValueError("No JSON")
    resp.text = ""
    
    req = MagicMock()
    req.method = "POST"
    req.url = "http://example.com/api"
    resp.request = req
    
    err_msg = _extract_api_error(resp, operation_id="test_op")
    assert "HTTP 404 (Not Found) on POST http://example.com/api" in err_msg

def test_extract_api_error_empty_json_dict():
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 400
    resp.reason = "Bad Request"
    resp.json.return_value = {}
    resp.text = "{}"
    
    req = MagicMock()
    req.method = "GET"
    req.url = "http://example.com/api/get"
    resp.request = req
    
    err_msg = _extract_api_error(resp, operation_id="get_op")
    assert "HTTP 400 (Bad Request) on GET http://example.com/api/get" in err_msg

@pytest.mark.asyncio
async def test_validator_node_rich_execution_error():
    mock_llm = AsyncMock()
    graph = build_dynamic_graph(mock_llm)
    validator_node = graph.nodes["validator"].bound
    
    state = {
        "response": {
            "status": "error",
            "status_code": 404,
            "raw_body": "User not found",
            "message": "Resource missing"
        },
        "retry_count": 1,
        "replan_count": 2,
        "resolved_args": {"userId": "U123"},
        "unresolved_required": []
    }
    
    result = await validator_node.ainvoke(state)
    assert result.get("response") is not None
    assert result["response"]["status"] == "error"
    expected_msg = "Execution failed (404): Resource missing | args={'userId': 'U123'}"
    assert result["response"]["message"] == expected_msg

@pytest.mark.asyncio
async def test_validator_node_terminal_error_populates_failure_memory_when_goal_set():
    """When a goal-oriented run hits a terminal error, the validator must
    populate last_failure/replan_context (not just append to step_results)
    so goap_goal's repeated-failure guard has real data to compare against
    on the next loop iteration, instead of always seeing None and never
    detecting no-progress replans."""
    mock_llm = AsyncMock()
    graph = build_dynamic_graph(mock_llm)
    validator_node = graph.nodes["validator"].bound

    state = {
        "goal": "get the first jules session",
        "response": {
            "status": "error",
            "status_code": 500,
            "message": "Internal DB Error",
        },
        "retry_count": 1,
        "replan_count": 0,
        "resolved_args": {"sessionId": "Jules"},
        "unresolved_required": [],
        "selected": {"operation_id": "jules_plugin__julesget_session"},
        "step_results": [],
        "replan_context": [],
    }

    result = await validator_node.ainvoke(state)
    assert result["response"]["status"] == "error"
    assert result["step_results"][-1] == result["response"]
    assert result["last_failure"]["outcome"] == "error"
    assert result["last_failure"]["operation_id"] == "jules_plugin__julesget_session"
    assert len(result["replan_context"]) == 1
    assert result["replan_context"][0] is result["last_failure"]


@pytest.mark.asyncio
async def test_validator_node_rich_execution_error_raw_body_fallback():
    mock_llm = AsyncMock()
    graph = build_dynamic_graph(mock_llm)
    validator_node = graph.nodes["validator"].bound
    
    state = {
        "response": {
            "status": "error",
            "status_code": 500,
            "raw_body": "Internal DB Error",
            "message": ""
        },
        "retry_count": 1,
        "replan_count": 2,
        "resolved_args": {"workspaceId": 99},
        "unresolved_required": []
    }
    
    result = await validator_node.ainvoke(state)
    assert result.get("response") is not None
    assert result["response"]["status"] == "error"
    expected_msg = "Execution failed (500): Internal DB Error | args={'workspaceId': 99}"
    assert result["response"]["message"] == expected_msg
