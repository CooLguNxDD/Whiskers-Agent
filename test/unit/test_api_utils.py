import datetime
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from requests.exceptions import HTTPError, ConnectionError
import requests

from utils.api_utils import sanitize_params, sanitize_body, api_error_dict, inject_pagination_defaults, safe_api_call

# --- sanitize_params tests ---

def test_sanitize_params_removes_none():
    assert sanitize_params({"a": None, "b": "v"}) == {"b": "v"}

def test_sanitize_params_coerces_bool():
    assert sanitize_params({"a": True, "b": False}) == {"a": "true", "b": "false"}

def test_sanitize_params_json_encodes_dict():
    assert sanitize_params({"filters": {"k": "v"}}) == {"filters": '{"k": "v"}'}

def test_sanitize_params_removes_undefined_string():
    assert sanitize_params({"a": "undefined", "b": "v"}) == {"b": "v"}

def test_sanitize_params_datetime():
    dt = datetime.datetime(2025, 8, 7, 9, 0, tzinfo=datetime.timezone.utc)
    d = datetime.date(2025, 8, 7)
    res = sanitize_params({"time": dt, "day": d})
    assert res == {"time": "2025-08-07T09:00:00Z", "day": "2025-08-07"}

def test_sanitize_params_nested_datetime():
    dt = datetime.datetime(2025, 8, 7, 9, 0, tzinfo=datetime.timezone.utc)
    res = sanitize_params({"details": {"time": dt}})
    assert res == {"details": '{"time": "2025-08-07T09:00:00Z"}'}

# --- sanitize_body tests ---

def test_sanitize_body_preserves_native_types():
    assert sanitize_body({"a": 1, "b": True, "c": [1, 2]}) == {"a": 1, "b": True, "c": [1, 2]}

def test_sanitize_body_removes_none_and_undefined():
    assert sanitize_body({"a": None, "b": "undefined", "c": 1}) == {"c": 1}

def test_sanitize_body_datetime():
    dt = datetime.datetime(2025, 8, 7, 9, 0, tzinfo=datetime.timezone.utc)
    d = datetime.date(2025, 8, 7)
    res = sanitize_body({"time": dt, "day": d})
    assert res == {"time": "2025-08-07T09:00:00Z", "day": "2025-08-07"}

def test_sanitize_body_nested_datetime():
    dt = datetime.datetime(2025, 8, 7, 9, 0, tzinfo=datetime.timezone.utc)
    res = sanitize_body({"details": {"time": dt}})
    assert res == {"details": {"time": "2025-08-07T09:00:00Z"}}

# --- api_error_dict tests ---

def test_api_error_dict_json_error_body():
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 400
    resp.json.return_value = {"message": "Bad"}
    
    err = api_error_dict(resp, context="Ctx")
    assert err["message"] == "Ctx: Bad"
    assert err["http_status"] == 400

def test_api_error_dict_text_fallback():
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 500
    resp.json.side_effect = ValueError("no json")
    resp.text = "raw error text"
    
    err = api_error_dict(resp, context="Ctx")
    assert err["message"] == "Ctx: raw error text"

def test_api_error_dict_nested_errors_list():
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 422
    resp.json.return_value = {"errors": [{"message": "field required"}]}
    
    err = api_error_dict(resp, context="Ctx")
    assert err["message"] == "Ctx: field required"

def test_api_error_dict_truncates_raw_body():
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 400
    resp.json.side_effect = ValueError("no json")
    resp.text = "A" * 3000
    
    err = api_error_dict(resp, context="Ctx")
    assert len(err["raw_body"]) == 2001
    assert err["raw_body"].endswith("…")
    assert err["raw_body"] == ("A" * 2000) + "…"

# --- inject_pagination_defaults tests ---

def test_inject_pagination_defaults_adds_defaults():
    res = inject_pagination_defaults({}, {"paginated": True})
    assert res["pageSize"] == 20  # assuming 20 is default
    assert res["pageIndex"] == 1

def test_inject_pagination_defaults_respects_caller_values():
    res = inject_pagination_defaults({"pageSize": 10}, {"paginated": True})
    assert res == {"pageSize": 10}

def test_inject_pagination_defaults_noop_when_not_paginated():
    res = inject_pagination_defaults({}, {"paginated": False})
    assert res == {}

# --- safe_api_call tests ---

@pytest.mark.asyncio
@patch("utils.api_utils.asyncio.to_thread")
async def test_safe_api_call_success(mock_to_thread):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    mock_to_thread.return_value = resp
    
    def on_success(r):
        return {"data": "ok"}
        
    res = await safe_api_call(lambda: resp, on_success, raw_response=True)
    assert res == {"data": "ok"}

@pytest.mark.asyncio
@patch("utils.api_utils.asyncio.to_thread")
async def test_safe_api_call_http_error_legacy(mock_to_thread):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 400
    resp.json.return_value = {"message": "bad request"}
    resp.raise_for_status.side_effect = HTTPError("400 Bad Request")
    mock_to_thread.return_value = resp
    
    res = await safe_api_call(lambda: resp, lambda r: r.json(), raw_response=True, raise_tool_error=False)
    assert isinstance(res, dict)
    assert res["status"] == "error"
    assert res["http_status"] == 400
    assert "bad request" in res["message"]


@pytest.mark.asyncio
@patch("utils.api_utils.asyncio.to_thread")
async def test_safe_api_call_http_error_raise(mock_to_thread):
    from fastmcp.exceptions import ToolError
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 400
    resp.json.return_value = {"message": "bad request"}
    resp.raise_for_status.side_effect = HTTPError("400 Bad Request")
    mock_to_thread.return_value = resp
    
    with pytest.raises(ToolError) as exc_info:
        await safe_api_call(lambda: resp, lambda r: r.json(), raw_response=True, raise_tool_error=True)
    assert "bad request" in str(exc_info.value)


@pytest.mark.asyncio
@patch("utils.api_utils.asyncio.to_thread")
async def test_safe_api_call_500_propagates(mock_to_thread):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 500
    resp.json.side_effect = ValueError()
    resp.raise_for_status.side_effect = HTTPError("500 Internal Server Error")
    mock_to_thread.return_value = resp
    
    with pytest.raises(HTTPError):
        await safe_api_call(lambda: resp, lambda r: r.json(), raw_response=True, raise_tool_error=True)


@pytest.mark.asyncio
@patch("utils.api_utils.asyncio.to_thread")
async def test_safe_api_call_connection_error_legacy(mock_to_thread):
    mock_to_thread.side_effect = ConnectionError("Connection refused")
    
    res = await safe_api_call(lambda: None, lambda r: r, raw_response=True, raise_tool_error=False)
    assert isinstance(res, dict)
    assert res["status"] == "error"
    assert res["error"] == "request_failed"
    assert "Connection refused" in res["message"]


@pytest.mark.asyncio
@patch("utils.api_utils.asyncio.to_thread")
async def test_safe_api_call_connection_error_raise(mock_to_thread):
    from fastmcp.exceptions import ToolError
    mock_to_thread.side_effect = ConnectionError("Connection refused")
    
    with pytest.raises(ToolError) as exc_info:
        await safe_api_call(lambda: None, lambda r: r, raw_response=True, raise_tool_error=True)
    assert "Connection refused" in str(exc_info.value)

@pytest.mark.asyncio
@patch("utils.api_utils.asyncio.to_thread")
async def test_safe_api_call_401_triggers_retry(mock_to_thread):
    resp_401 = MagicMock(spec=requests.Response)
    resp_401.status_code = 401
    resp_401.raise_for_status.side_effect = HTTPError("401 Unauthorized")
    
    resp_200 = MagicMock(spec=requests.Response)
    resp_200.status_code = 200
    
    mock_to_thread.side_effect = [resp_401, resp_200]
    
    headers = {"Authorization": "old"}
    
    async def retry_auth():
        return {"Authorization": "new"}
        
    def on_success(r):
        return {"data": "ok"}
        
    res = await safe_api_call(
        lambda: resp_200, 
        on_success, 
        raw_response=True, 
        auth_retry=retry_auth, 
        headers=headers
    )
    
    assert res == {"data": "ok"}
    assert headers["Authorization"] == "new"
