"""Generic in-process agent tool-calling loop (core_graph.agent_loop)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core_graph.agent_loop.runner import (
    AgentRunResult,
    _authorize_fast_path,
    _dispatch_tool,
    _extract_text,
    _parse_final_output,
    run_agent,
)
from core_graph.agent_loop.spec import AgentSpec, ToolRef
from core_graph.agent_loop.toolset import build_toolset, to_langchain_tool_schemas


def _op(plugin_id: str, operation_id: str, tags=(), description="", input_schema=None):
    return SimpleNamespace(
        plugin_id=plugin_id,
        operation_id=operation_id,
        tags=tags,
        description=description,
        input_schema=input_schema or {"type": "object", "properties": {}},
    )


class _FakeCatalog:
    def __init__(self, ops):
        self._ops = ops

    def all(self):
        return self._ops

    def get(self, plugin_id, operation_id):
        for op in self._ops:
            if op.plugin_id == plugin_id and op.operation_id == operation_id:
                return op
        return None


def test_build_toolset_exact_ref():
    ops = [_op("portfolio_plugin", "portfolio_plugin__list_owned_repos")]
    refs = [ToolRef("portfolio_plugin", "*list_owned_repos")]
    bound = build_toolset(refs, catalog=_FakeCatalog(ops))
    assert len(bound) == 1
    assert bound[0].plugin_id == "portfolio_plugin"
    assert bound[0].operation_id == "portfolio_plugin__list_owned_repos"


def test_build_toolset_glob_expansion_proxy():
    ops = [
        _op("proxy_github-1", "search_repositories"),
        _op("proxy_github-1", "get_repo"),
        _op("proxy_notion-1", "notion-search"),
        _op("fake_plugin", "unrelated_tool"),
    ]
    refs = [ToolRef("proxy_github*", "*")]
    bound = build_toolset(refs, catalog=_FakeCatalog(ops))
    names = {(b.plugin_id, b.operation_id) for b in bound}
    assert ("proxy_github-1", "search_repositories") in names
    assert ("proxy_github-1", "get_repo") in names
    assert not any(pid == "proxy_notion-1" for pid, _ in names)
    assert not any(pid == "fake_plugin" for pid, _ in names)


def test_build_toolset_max_tools_cap():
    ops = [_op("proxy_github-1", f"op_{i}") for i in range(10)]
    refs = [ToolRef("proxy_github*", "*")]
    bound = build_toolset(refs, catalog=_FakeCatalog(ops), max_tools=3)
    assert len(bound) == 3


def test_build_toolset_catalog_unavailable_fails_open():
    def _boom():
        raise RuntimeError("no catalog")

    with patch("core.route_registry.operation_catalog.get_operation_catalog", _boom):
        bound = build_toolset([ToolRef("proxy_github*", "*")])
    assert bound == []


def test_to_langchain_tool_schemas_shape():
    ops = [_op("portfolio_plugin", "portfolio_plugin__list_owned_repos", description="lists repos")]
    bound = build_toolset([ToolRef("portfolio_plugin", "*list_owned_repos")], catalog=_FakeCatalog(ops))
    schemas = to_langchain_tool_schemas(bound)
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "portfolio_plugin__list_owned_repos"
    assert schemas[0]["function"]["description"] == "lists repos"


def test_to_langchain_tool_schemas_sanitizes_non_string_enum():
    """Regression: a proxy schema with a boolean-valued enum (e.g. a flag param
    shaped as {"enum": [true, false]}) crashes Google genai's schema converter,
    which only accepts string enum values — takes down the whole tool-calling
    turn for every bound tool, not just the offending one. Verified live against
    a real mounted GitHub Copilot MCP proxy."""
    schema = {
        "type": "object",
        "properties": {
            "recursive": {"type": "boolean", "enum": [True, False]},
            "nested": {
                "type": "array",
                "items": {"type": "object", "properties": {"flag": {"enum": [True, 1, "x"]}}},
            },
        },
    }
    ops = [_op("proxy_github-1", "some_op", input_schema=schema)]
    bound = build_toolset([ToolRef("proxy_github*", "*")], catalog=_FakeCatalog(ops))
    schemas = to_langchain_tool_schemas(bound)
    params = schemas[0]["function"]["parameters"]
    assert params["properties"]["recursive"]["enum"] == ["true", "false"]
    nested_enum = params["properties"]["nested"]["items"]["properties"]["flag"]["enum"]
    assert nested_enum == ["true", "1", "x"]
    assert all(isinstance(v, str) for v in nested_enum)


def test_extract_text_handles_content_block_list():
    """Regression: Gemini/Anthropic return AIMessage.content as a list of content
    blocks, not a bare string — str()'ing that list yields a Python repr (single
    quotes, None/True literals) that never parses as JSON."""
    content = [{"type": "text", "text": '```json\n{"findings": []}\n```', "extras": {"x": 1}}]
    assert _extract_text(content) == '```json\n{"findings": []}\n```'
    assert _parse_final_output(content, {"type": "object"}) == {"findings": []}


def test_extract_text_plain_string_unchanged():
    assert _extract_text("hello") == "hello"
    assert _extract_text(None) == ""


class _FakeAIMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeBoundLLM:
    """Scripted sequence of ainvoke() responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return self._responses.pop(0)


class _FakeLLM:
    def __init__(self, bound):
        self._bound = bound

    def bind_tools(self, schemas):
        return self._bound


@pytest.mark.asyncio
async def test_run_agent_dispatches_tool_then_finishes():
    ops = [_op("portfolio_plugin", "portfolio_plugin__list_owned_repos")]
    spec = AgentSpec(
        name="test_agent",
        system_prompt="sys",
        tools=[ToolRef("portfolio_plugin", "*list_owned_repos")],
        max_steps=5,
        output_schema={"type": "object", "properties": {"findings": {"type": "array"}}},
    )
    bound = _FakeBoundLLM(
        [
            _FakeAIMessage(
                tool_calls=[{"name": "portfolio_plugin__list_owned_repos", "args": {}, "id": "1"}]
            ),
            _FakeAIMessage(content='{"findings": [{"slug": "a"}]}'),
        ]
    )
    fake_llm = _FakeLLM(bound)

    async def _fake_get_llm(kind):
        return fake_llm

    async def _fake_dispatch(plugin_id, operation_id, args):
        return {"status": "ok", "repos": [{"full_name": "x/y"}]}

    with patch("core.route_registry.operation_catalog.get_operation_catalog", lambda: _FakeCatalog(ops)), \
         patch("core_graph.agent_loop.runner._get_llm", _fake_get_llm), \
         patch("core_graph.agent_loop.runner._dispatch_tool", _fake_dispatch):
        result: AgentRunResult = await run_agent(spec, "do it", tenant_id=1)

    assert result.status == "ok"
    assert result.output == {"findings": [{"slug": "a"}]}
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "portfolio_plugin__list_owned_repos"


@pytest.mark.asyncio
async def test_run_agent_budget_exhaustion_returns_partial():
    ops = [_op("portfolio_plugin", "portfolio_plugin__list_owned_repos")]
    spec = AgentSpec(
        name="test_agent",
        system_prompt="sys",
        tools=[ToolRef("portfolio_plugin", "*list_owned_repos")],
        max_steps=2,
    )
    # Every turn calls a tool — never terminates on its own.
    bound = _FakeBoundLLM(
        [
            _FakeAIMessage(tool_calls=[{"name": "portfolio_plugin__list_owned_repos", "args": {}, "id": "1"}]),
            _FakeAIMessage(tool_calls=[{"name": "portfolio_plugin__list_owned_repos", "args": {}, "id": "2"}]),
        ]
    )
    fake_llm = _FakeLLM(bound)

    async def _fake_get_llm(kind):
        return fake_llm

    async def _fake_dispatch(plugin_id, operation_id, args):
        return {"status": "ok"}

    with patch("core.route_registry.operation_catalog.get_operation_catalog", lambda: _FakeCatalog(ops)), \
         patch("core_graph.agent_loop.runner._get_llm", _fake_get_llm), \
         patch("core_graph.agent_loop.runner._dispatch_tool", _fake_dispatch):
        result = await run_agent(spec, "do it", tenant_id=1)

    assert result.status == "partial"
    assert len(result.tool_calls) == 2
    assert "max_steps_exhausted" in result.errors


@pytest.mark.asyncio
async def test_run_agent_unknown_tool_call_is_soft_error():
    ops = [_op("portfolio_plugin", "portfolio_plugin__list_owned_repos")]
    spec = AgentSpec(
        name="test_agent",
        system_prompt="sys",
        tools=[ToolRef("portfolio_plugin", "*list_owned_repos")],
        max_steps=3,
    )
    bound = _FakeBoundLLM(
        [
            _FakeAIMessage(tool_calls=[{"name": "not_a_real_tool", "args": {}, "id": "1"}]),
            _FakeAIMessage(content="{}"),
        ]
    )
    fake_llm = _FakeLLM(bound)

    async def _fake_get_llm(kind):
        return fake_llm

    with patch("core.route_registry.operation_catalog.get_operation_catalog", lambda: _FakeCatalog(ops)), \
         patch("core_graph.agent_loop.runner._get_llm", _fake_get_llm):
        result = await run_agent(spec, "do it", tenant_id=1)

    assert result.tool_calls[0]["result"]["status"] == "error"
    assert "unknown tool" in result.tool_calls[0]["result"]["message"]


@pytest.mark.asyncio
async def test_run_agent_llm_unavailable_returns_error():
    spec = AgentSpec(name="test_agent", system_prompt="sys", tools=[])

    async def _boom(kind):
        raise RuntimeError("no llm configured")

    with patch("core_graph.agent_loop.runner._get_llm", _boom):
        result = await run_agent(spec, "do it", tenant_id=1)

    assert result.status == "error"
    assert any("llm_unavailable" in e for e in result.errors)


@pytest.mark.asyncio
async def test_run_agent_propagates_caller_scopes():
    ops = [_op("portfolio_plugin", "portfolio_plugin__list_owned_repos")]
    spec = AgentSpec(
        name="test_agent",
        system_prompt="sys",
        tools=[ToolRef("portfolio_plugin", "*list_owned_repos")],
        max_steps=2,
        caller_scopes=["read:portfolio", "write:portfolio"],
    )
    bound = _FakeBoundLLM(
        [
            _FakeAIMessage(
                tool_calls=[{"name": "portfolio_plugin__list_owned_repos", "args": {}, "id": "1"}]
            ),
            _FakeAIMessage(content="{}"),
        ]
    )
    fake_llm = _FakeLLM(bound)

    async def _fake_get_llm(kind):
        return fake_llm

    dispatch_scopes = []
    async def _fake_dispatch(plugin_id, operation_id, args, caller_scopes=None):
        dispatch_scopes.append(caller_scopes)
        return {"status": "ok"}

    with patch("core.route_registry.operation_catalog.get_operation_catalog", lambda: _FakeCatalog(ops)), \
         patch("core_graph.agent_loop.runner._get_llm", _fake_get_llm), \
         patch("core_graph.agent_loop.runner._dispatch_tool", _fake_dispatch):
        await run_agent(spec, "do it", tenant_id=1)

    assert dispatch_scopes == [["read:portfolio", "write:portfolio"]]


@pytest.mark.asyncio
async def test_run_json_protocol_status_output_mismatch():
    spec = AgentSpec(
        name="test_agent",
        system_prompt="sys",
        tools=[],
        max_steps=2,
        output_schema={"type": "object"},
    )
    
    class _FakeCliLLM:
        _llm_type = "claude-cli"
        def __init__(self):
            self.responses = [_FakeAIMessage(content='{"final": "not-a-dict"}')]
            
        async def ainvoke(self, messages):
            return self.responses.pop(0)

    fake_llm = _FakeCliLLM()

    async def _fake_get_llm(kind):
        return fake_llm

    with patch("core_graph.agent_loop.runner._get_llm", _fake_get_llm):
        result = await run_agent(spec, "do it", tenant_id=1)

    assert result.status == "partial"
    assert result.output is None


def _fake_execute_error(status=500, code="internal"):
    from core.route_registry.execute import ExecuteError

    return ExecuteError(code, "boom", status=status)


@pytest.mark.asyncio
async def test_dispatch_tool_fast_path_denies_when_op_unresolvable():
    """Fail-closed: execute_operation raises non-403, fast-path callable exists,
    but catalog/route lookup can't resolve op metadata — must deny, never call fn."""
    from core.context import route_registry as rr

    called = {"n": 0}

    def _fast_path_callable(*a, **k):
        async def _fn(**kwargs):
            called["n"] += 1
            return {"status": "ok"}
        return _fn

    with (
        patch(
            "core.route_registry.execute.execute_operation",
            AsyncMock(side_effect=_fake_execute_error()),
        ),
        patch.object(rr, "fast_path_callable", _fast_path_callable),
        patch.object(rr, "get", lambda *a, **k: None),
        patch(
            "core.route_registry.operation_catalog.get_operation_catalog",
            lambda: _FakeCatalog([]),
        ),
    ):
        result = await _dispatch_tool("some_plugin", "some_op", {}, caller_scopes=["read:x"])

    assert result["status"] == "error"
    assert "Forbidden" in result["message"]
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_dispatch_tool_fast_path_denies_on_scope_check_exception():
    """Fail-closed: a scope-evaluation error must deny, not proceed to invoke the tool."""
    from core.context import route_registry as rr

    called = {"n": 0}
    op = _op("some_plugin", "some_op")
    op.required_scopes = ["read:some_plugin"]

    def _fast_path_callable(*a, **k):
        async def _fn(**kwargs):
            called["n"] += 1
            return {"status": "ok"}
        return _fn

    class _CatalogRaises:
        def get(self, plugin_id, operation_id):
            return op

    with (
        patch(
            "core.route_registry.execute.execute_operation",
            AsyncMock(side_effect=_fake_execute_error()),
        ),
        patch.object(rr, "fast_path_callable", _fast_path_callable),
        patch(
            "core.route_registry.operation_catalog.get_operation_catalog",
            lambda: _CatalogRaises(),
        ),
        patch("core.scope_management.is_allowed", side_effect=RuntimeError("scope service down")),
    ):
        result = await _dispatch_tool("some_plugin", "some_op", {}, caller_scopes=["read:x"])

    assert result["status"] == "error"
    assert "Forbidden" in result["message"]
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_dispatch_tool_invoke_failed_does_not_retry_fast_path():
    """A callable that already ran and raised (ExecuteError code=invoke_failed) must
    not be re-invoked through the fast-path fallback — that would dispatch the
    upstream call a second time and double any side effects."""
    from core.context import route_registry as rr

    called = {"n": 0}

    def _fast_path_callable(*a, **k):
        async def _fn(**kwargs):
            called["n"] += 1
            return {"status": "ok"}
        return _fn

    with (
        patch(
            "core.route_registry.execute.execute_operation",
            AsyncMock(side_effect=_fake_execute_error(status=500, code="invoke_failed")),
        ),
        patch.object(rr, "fast_path_callable", _fast_path_callable),
    ):
        result = await _dispatch_tool("some_plugin", "some_op", {}, caller_scopes=["read:x"])

    assert result["status"] == "error"
    assert result["message"] == "boom"
    assert called["n"] == 0


def test_authorize_fast_path_allows_when_scopes_sufficient():
    op = _op("some_plugin", "some_op", tags=())
    op.required_scopes = ["read:some_plugin"]

    class _Catalog:
        def get(self, plugin_id, operation_id):
            return op

    with (
        patch(
            "core.route_registry.operation_catalog.get_operation_catalog",
            lambda: _Catalog(),
        ),
        patch("core.scope_management.is_allowed", return_value=True),
    ):
        deny = _authorize_fast_path("some_plugin", "some_op", ["read:some_plugin"])

    assert deny is None
