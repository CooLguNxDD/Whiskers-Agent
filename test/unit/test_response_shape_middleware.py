"""Unit tests for ResponseShapeMiddleware (direct MCP tool-call shaping)."""

from __future__ import annotations

import pytest
import mcp.types as mt
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from core.context.response_shape_middleware import ResponseShapeMiddleware
from core.context.tool_meta import ToolMeta


def _ctx(name: str, arguments: dict | None = None) -> MiddlewareContext:
    return MiddlewareContext(message=mt.CallToolRequestParams(name=name, arguments=arguments or {}))


def _enabled_cfg(monkeypatch, *, enabled: bool = True, exclude: list | None = None):
    class _Cfg:
        server = {"graph": {"direct_call_shaping": enabled, "direct_call_shaping_exclude": exclude or []}}

    monkeypatch.setattr("utils.config_registry.get_config_registry", lambda: _Cfg())


def _no_schema_declared(monkeypatch):
    monkeypatch.setattr(
        "core.context.response_shape_middleware._tool_declares_response_shape",
        lambda *a, **k: _async_false(),
    )


async def _async_false():
    return False


async def _async_true():
    return True


@pytest.mark.asyncio
async def test_shapes_proxy_tool_result_to_csv(monkeypatch):
    """A direct call on a namespaced proxy tool gets shaped via apply_response_shape."""
    _enabled_cfg(monkeypatch)
    _no_schema_declared(monkeypatch)

    async def fake_resolve_meta(*a, **k):
        return ToolMeta("proxy_github-andrew", ("proxy_github-andrew", "proxy"), False)

    monkeypatch.setattr(
        "core.context.tool_meta.resolve_tool_meta", fake_resolve_meta
    )

    calls = []

    async def fake_apply(payload, *, operation_id, tool_name, request_params, shape):
        calls.append({"operation_id": operation_id, "shape": shape, "request_params": request_params})
        return "id,title\n1,pr-one"

    monkeypatch.setattr("utils.api_utils.apply_response_shape", fake_apply)

    async def call_next(ctx):
        return ToolResult(content=[], structured_content={"items": [{"id": 1, "title": "pr-one"}]})

    mw = ResponseShapeMiddleware()
    ctx = _ctx("github-andrew_list_pull_requests", {"projectId": "p1"})
    out = await mw.on_call_tool(ctx, call_next)

    assert out.structured_content == {"result": "id,title\n1,pr-one"}
    assert calls[0]["operation_id"] == "list_pull_requests"
    assert calls[0]["shape"] is None


@pytest.mark.asyncio
async def test_response_shape_override_not_forwarded_upstream_and_restores_json(monkeypatch):
    """_response_shape is stripped before call_next and passed as the shape override."""
    _enabled_cfg(monkeypatch)
    _no_schema_declared(monkeypatch)

    async def fake_resolve_meta(*a, **k):
        return ToolMeta("proxy_github-andrew", (), False)

    monkeypatch.setattr(
        "core.context.tool_meta.resolve_tool_meta", fake_resolve_meta
    )

    seen_args = {}

    async def call_next(ctx):
        seen_args["arguments"] = ctx.message.arguments
        return ToolResult(content=[], structured_content={"items": [{"id": 1}]})

    async def fake_apply(payload, *, operation_id, tool_name, request_params, shape):
        assert shape == {"response_format": "json"}
        return payload

    monkeypatch.setattr("utils.api_utils.apply_response_shape", fake_apply)

    mw = ResponseShapeMiddleware()
    ctx = _ctx(
        "github-andrew_list_pull_requests",
        {"projectId": "p1", "_response_shape": {"response_format": "json"}},
    )
    out = await mw.on_call_tool(ctx, call_next)

    assert "_response_shape" not in seen_args["arguments"]
    assert seen_args["arguments"] == {"projectId": "p1"}
    assert out.structured_content == {"items": [{"id": 1}]}


@pytest.mark.asyncio
async def test_skips_tool_that_already_declares_response_shape(monkeypatch):
    """Generated safe_api_call tools shape internally — no double-shaping."""
    _enabled_cfg(monkeypatch)

    monkeypatch.setattr(
        "core.context.response_shape_middleware._tool_declares_response_shape",
        lambda *a, **k: _async_true(),
    )

    called = {"apply": False}

    async def fake_apply(*a, **k):
        called["apply"] = True
        return {}

    monkeypatch.setattr("utils.api_utils.apply_response_shape", fake_apply)

    original = ToolResult(content=[], structured_content={"raw": "already-shaped-csv"})

    async def call_next(ctx):
        return original

    mw = ResponseShapeMiddleware()
    out = await mw.on_call_tool(_ctx("get_list_sessions", {}), call_next)

    assert out is original
    assert called["apply"] is False


@pytest.mark.asyncio
async def test_run_graph_untouched():
    """GATEWAY_ALWAYS_VISIBLE tools bypass shaping entirely."""
    original = ToolResult(content=[], structured_content={"summary": "done"})

    async def call_next(ctx):
        return original

    mw = ResponseShapeMiddleware()
    out = await mw.on_call_tool(_ctx("run_graph", {}), call_next)
    assert out is original


@pytest.mark.asyncio
async def test_kill_switch_disables_shaping(monkeypatch):
    """graph.direct_call_shaping = false skips shaping entirely."""
    _enabled_cfg(monkeypatch, enabled=False)

    called = {"apply": False}

    async def fake_apply(*a, **k):
        called["apply"] = True
        return {}

    monkeypatch.setattr("utils.api_utils.apply_response_shape", fake_apply)

    original = ToolResult(content=[], structured_content={"a": 1})

    async def call_next(ctx):
        return original

    mw = ResponseShapeMiddleware()
    out = await mw.on_call_tool(_ctx("some_tool", {}), call_next)

    assert out is original
    assert called["apply"] is False


@pytest.mark.asyncio
async def test_excluded_tool_name_skips_shaping(monkeypatch):
    """graph.direct_call_shaping_exclude lists a tool name to leave untouched."""
    _enabled_cfg(monkeypatch, exclude=["design_layout"])
    _no_schema_declared(monkeypatch)

    called = {"apply": False}

    async def fake_apply(*a, **k):
        called["apply"] = True
        return {}

    monkeypatch.setattr("utils.api_utils.apply_response_shape", fake_apply)

    original = ToolResult(content=[], structured_content={"layout": {}})

    async def call_next(ctx):
        return original

    mw = ResponseShapeMiddleware()
    out = await mw.on_call_tool(_ctx("design_layout", {}), call_next)

    assert out is original
    assert called["apply"] is False


@pytest.mark.asyncio
async def test_shaping_exception_falls_back_to_original_result(monkeypatch):
    """Any shaping failure returns the original ToolResult unchanged."""
    _enabled_cfg(monkeypatch)
    _no_schema_declared(monkeypatch)

    async def fake_resolve_meta(*a, **k):
        return ToolMeta("jules_plugin", (), False)

    monkeypatch.setattr(
        "core.context.tool_meta.resolve_tool_meta", fake_resolve_meta
    )

    async def boom(*a, **k):
        raise RuntimeError("shape failed")

    monkeypatch.setattr("utils.api_utils.apply_response_shape", boom)

    original = ToolResult(content=[], structured_content={"a": 1})

    async def call_next(ctx):
        return original

    mw = ResponseShapeMiddleware()
    out = await mw.on_call_tool(_ctx("julesget_session", {}), call_next)
    assert out is original


@pytest.mark.asyncio
async def test_call_next_circular_reference_error_does_not_crash_session(monkeypatch):
    """Regression: a tool whose return value contains a genuine Python object
    cycle (e.g. an ORM back-reference) makes FastMCP's own ``ToolResult``
    construction raise ``ValueError: Circular reference detected`` *inside*
    ``call_next`` — before this middleware even has a ``result`` to shape.
    Left unguarded, that propagates out of ``on_call_tool`` and crashes the
    whole MCP session (observed in prod: "Session <id> crashed"). The
    middleware must convert it into a normal tool-error result instead.
    """
    _enabled_cfg(monkeypatch)

    async def call_next(ctx):
        raise ValueError("Circular reference detected (id repeated)")

    mw = ResponseShapeMiddleware()
    out = await mw.on_call_tool(_ctx("upsert_project", {}), call_next)

    assert isinstance(out, ToolResult)
    assert out.is_error is True
    assert out.structured_content["error"] == "circular_reference"
    import json
    json.dumps(out.structured_content)  # must not raise


@pytest.mark.asyncio
async def test_call_next_unrelated_value_error_is_not_swallowed(monkeypatch):
    """Only the circular-reference case is special-cased; any other ValueError
    from call_next must still propagate normally."""
    _enabled_cfg(monkeypatch)

    async def call_next(ctx):
        raise ValueError("some unrelated tool validation failure")

    mw = ResponseShapeMiddleware()
    with pytest.raises(ValueError, match="unrelated tool validation failure"):
        await mw.on_call_tool(_ctx("some_tool", {}), call_next)


@pytest.mark.asyncio
async def test_on_list_tools_injects_response_shape_property_on_covered_tools(monkeypatch):
    """Covered tools get an advertised _response_shape param; run_graph does not."""
    _enabled_cfg(monkeypatch)

    class _FakeTool:
        def __init__(self, name, parameters):
            self.name = name
            self.parameters = parameters

        def model_copy(self, update):
            new = _FakeTool(self.name, update.get("parameters", self.parameters))
            return new

    proxy_tool = _FakeTool("github-andrew_list_pull_requests", {"properties": {"projectId": {"type": "string"}}})
    gateway_tool = _FakeTool("run_graph", {"properties": {}})
    already_declared = _FakeTool("get_list_sessions", {"properties": {"_response_shape": {"type": "object"}}})

    async def call_next(ctx):
        return [proxy_tool, gateway_tool, already_declared]

    mw = ResponseShapeMiddleware()
    out = await mw.on_list_tools(_ctx("list_tools"), call_next)

    by_name = {t.name: t for t in out}
    assert "_response_shape" in by_name["github-andrew_list_pull_requests"].parameters["properties"]
    assert "_response_shape" not in by_name["run_graph"].parameters["properties"]
    # already declared — untouched object identity preserved
    assert by_name["get_list_sessions"] is already_declared
    # description sourced from the core-owned hint module, not a hardcoded literal
    from utils.response_shape_hints import SHAPE_PARAM_DESCRIPTION
    injected = by_name["github-andrew_list_pull_requests"].parameters["properties"]["_response_shape"]
    assert injected["description"] == SHAPE_PARAM_DESCRIPTION

class _CyclicCustomObject:
    def __init__(self):
        self.child = None

@pytest.mark.asyncio
async def test_sanitize_unshaped_result_custom_object_cycle():
    """Verify _sanitize_unshaped_result correctly sanitizes cyclic custom objects."""
    from core.context.response_shape_middleware import _sanitize_unshaped_result
    from fastmcp.tools.base import ToolResult
    from fastmcp.prompts.base import TextContent
    from unittest.mock import MagicMock
    import json

    obj1 = _CyclicCustomObject()
    obj2 = _CyclicCustomObject()
    obj1.child = obj2
    obj2.child = obj1

    tr = MagicMock(spec=ToolResult)
    tr.structured_content = {"data": obj1}
    tr.meta = {}

    # We need to simulate that the custom object raises ValueError during the FIRST json.dumps test,
    # so that it falls through to strip_base64_fields + json.dumps.
    # Actually, default=str will not raise ValueError for custom objects, it will just return the repr!
    # Wait, the prompt said:
    #   "text = json.dumps(cleaned, default=str) succeeds anyway (default=str stringifies it),
    #    but then the caller sets structured_content=cleaned (the raw cyclic object)"
    # Ah! The issue was in the *except* block where `text = json.dumps(cleaned, default=str)` succeeds!

    # Let's mock the FIRST json.dumps to fail so it goes to the except block
    original_dumps = json.dumps
    def mock_dumps(obj, *args, **kwargs):
        if obj is tr.structured_content:
            raise TypeError("simulate failure on first dump")
        return original_dumps(obj, *args, **kwargs)

    import core.context.response_shape_middleware as rsm
    rsm.json.dumps = mock_dumps

    try:
        result = _sanitize_unshaped_result(tr, "test_tool")
        assert not getattr(result, "is_error", False)
        assert isinstance(result.structured_content["data"], str)
    finally:
        rsm.json.dumps = original_dumps

@pytest.mark.asyncio
async def test_sanitize_unshaped_result_fallback_fails(monkeypatch):
    """Verify _sanitize_unshaped_result returns is_error=True when second json.dumps fails."""
    from core.context.response_shape_middleware import _sanitize_unshaped_result
    from fastmcp.tools.base import ToolResult
    from fastmcp.prompts.base import TextContent
    from unittest.mock import MagicMock
    import json

    tr = MagicMock(spec=ToolResult)
    tr.structured_content = {"data": object()}
    tr.meta = {}

    original_dumps = json.dumps
    def mock_dumps(*args, **kwargs):
        if len(args) > 0 and isinstance(args[0], dict) and "data" in args[0] and type(args[0]["data"]) is object:
            raise TypeError("Cannot serialize object()")
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr("json.dumps", mock_dumps)

    result = _sanitize_unshaped_result(tr, "test_tool")

    assert getattr(result, "is_error", False) is True
    assert isinstance(result.structured_content, dict)
    assert result.structured_content.get("error") == "unserializable_result"
