"""Unit tests for proxy-tool response shaping (context optimization)."""

from __future__ import annotations

import pytest

from core.proxy_tools.proxy_tool_loader import _shape_proxy_result, make_proxy_callable


class _FakeText:
    def __init__(self, text: str):
        self.text = text


class _FakeResult:
    def __init__(self, text: str, is_error: bool = False):
        self.content = [_FakeText(text)]
        self.isError = is_error


class _FakeServer:
    def __init__(self, result):
        self._result = result

    async def call_tool(self, name, kwargs):
        return self._result


class _FakeProvider:
    def __init__(self, result):
        self.server = _FakeServer(result)


@pytest.mark.asyncio
async def test_shape_proxy_result_toggle_off(monkeypatch):
    """When proxy_response_shaping is false, return parsed payload unchanged."""
    class _Cfg:
        server = {"graph": {"proxy_response_shaping": False}}

    monkeypatch.setattr(
        "utils.config_registry.get_config_registry",
        lambda: _Cfg(),
    )
    raw = {"sessions": [{"id": "s1", "name": "n", "hex": "a" * 40}]}
    assert await _shape_proxy_result(raw, "ListSessions", {}) is raw


@pytest.mark.asyncio
async def test_shape_proxy_result_applies_pipeline(monkeypatch):
    """Success path routes through apply_response_shape with unqualified op id."""
    class _Cfg:
        server = {"graph": {"proxy_response_shaping": True}}

    monkeypatch.setattr(
        "utils.config_registry.get_config_registry",
        lambda: _Cfg(),
    )
    calls = []

    async def fake_apply(parsed, operation_id=None, tool_name=None, request_params=None, shape=None, session_id=None):
        calls.append(
            {
                "operation_id": operation_id,
                "tool_name": tool_name,
                "request_params": request_params,
                "shape": shape,
            }
        )
        return "id,name\ns1,n"

    monkeypatch.setattr("utils.api_utils.apply_response_shape", fake_apply)
    out = await _shape_proxy_result(
        {"sessions": [{"id": "s1", "name": "n"}]},
        "ListSessions",
        {"projectId": "p1"},
    )
    assert out == "id,name\ns1,n"
    assert calls == [
        {
            "operation_id": "ListSessions",
            "tool_name": "ListSessions",
            "request_params": {"projectId": "p1"},
            "shape": None,
        }
    ]


@pytest.mark.asyncio
async def test_shape_proxy_result_pass_through_on_error(monkeypatch):
    """Shaping exceptions never break the proxy call."""
    class _Cfg:
        server = {"graph": {"proxy_response_shaping": True}}

    monkeypatch.setattr(
        "utils.config_registry.get_config_registry",
        lambda: _Cfg(),
    )

    async def boom(*a, **k):
        raise RuntimeError("shape failed")

    monkeypatch.setattr("utils.api_utils.apply_response_shape", boom)
    raw = {"ok": True}
    assert await _shape_proxy_result(raw, "ListSessions", {}) is raw


@pytest.mark.asyncio
async def test_make_proxy_callable_shapes_json_success(monkeypatch):
    """JSON-success branch of call_proxy_tool runs the shape helper."""
    shaped = {"_shaped": True}

    async def _fake_shape(parsed, raw_op_id, request_params):
        return shaped

    monkeypatch.setattr(
        "core.proxy_tools.proxy_tool_loader._shape_proxy_result",
        _fake_shape,
    )
    provider = _FakeProvider(_FakeResult('{"id":"s1","name":"n"}'))
    fn = make_proxy_callable(provider, "ListSessions")
    out = await fn(projectId="p1")
    assert out is shaped
    assert getattr(fn, "_is_proxy_wrapper", False) is True


@pytest.mark.asyncio
async def test_make_proxy_callable_error_branch_raises_tool_error():
    """isError content raises ToolError so FastMCP reports a real failure, not a 200-OK body."""
    from fastmcp.exceptions import ToolError

    provider = _FakeProvider(_FakeResult("upstream failed", is_error=True))
    fn = make_proxy_callable(provider, "ListSessions")
    with pytest.raises(ToolError) as exc_info:
        await fn()
    assert "upstream failed" in str(exc_info.value)
    assert exc_info.value.api_error_type == "proxy_tool_failed"


@pytest.mark.asyncio
async def test_make_proxy_callable_transport_exception_raises_tool_error():
    """A raised exception from provider.server.call_tool also becomes a ToolError."""
    from fastmcp.exceptions import ToolError

    class _BoomServer:
        async def call_tool(self, name, kwargs):
            raise RuntimeError("connection reset")

    class _BoomProvider:
        server = _BoomServer()

    fn = make_proxy_callable(_BoomProvider(), "ListSessions")
    with pytest.raises(ToolError) as exc_info:
        await fn()
    assert "connection reset" in str(exc_info.value)


# ── envelope boundary (PR #227 review thread) ────────────────────────────────
# The proxy layer deliberately does NOT unwrap {_meta, data}: endpoint_meta._base
# sets include_meta, and external MCP clients are meant to keep the rich envelope.
# GOAP flattens it instead, at the point the result enters graph state. These two
# tests pin both halves of that contract so the split stays intentional.


@pytest.mark.asyncio
async def test_shape_proxy_result_preserves_meta_envelope(monkeypatch):
    """A shaped {_meta, data} envelope is returned verbatim, not flattened here."""
    class _Cfg:
        server = {"graph": {"proxy_response_shaping": True}}

    monkeypatch.setattr("utils.config_registry.get_config_registry", lambda: _Cfg())

    envelope = {"_meta": {"shaped": True}, "data": [{"id": "s1"}]}

    async def fake_apply(parsed, **kwargs):
        return envelope

    monkeypatch.setattr("utils.api_utils.apply_response_shape", fake_apply)
    out = await _shape_proxy_result({"sessions": []}, "ListSessions", {})
    assert out is envelope


def test_unwrap_discovery_envelope_flattens_at_graph_boundary():
    """GOAP unwraps the envelope so $steps[i] / $item.id chaining sees a flat payload."""
    from core_graph.node.execute_step import _unwrap_discovery_envelope

    data = [{"id": "s1"}, {"id": "s2"}]
    assert _unwrap_discovery_envelope({"_meta": {"shaped": True}, "data": data}) == data
    # A status-bearing tool result is a real payload, not an envelope — leave it.
    status_result = {"status": "ok", "_meta": {}, "data": data}
    assert _unwrap_discovery_envelope(status_result) is status_result
    # Non-envelope shapes pass through untouched.
    assert _unwrap_discovery_envelope(data) is data
    # CSV-shaped data (response_format: csv) is a bare string — unwrapping it
    # would make `response` fail every isinstance(..., dict) check downstream
    # and get hard-wrapped as a false status:"error" by _finalize_graph_response.
    csv_envelope = {"_meta": {"shaped": True}, "data": "a,b\n1,2"}
    assert _unwrap_discovery_envelope(csv_envelope) is csv_envelope
