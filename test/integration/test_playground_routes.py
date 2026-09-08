import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import api.playground_routes  # noqa — triggers @mcp.custom_route registration


def _sse_events(text: str) -> list[dict]:
    """Parse `data: {...}` SSE frames into a list of dicts."""
    events = []
    for part in text.split("\n\n"):
        part = part.strip()
        if part.startswith("data: "):
            events.append(json.loads(part[len("data: "):]))
    return events


class TestPlaygroundStreamGoap:
    @pytest.mark.asyncio
    async def test_stream_goap_emits_keepalive_without_killing_generator(self, client):
        """Slow streams must emit SSE keepalive comments and still complete."""
        async def slow_stream(_message, *args, **kwargs):
            await asyncio.sleep(0.15)
            yield {"type": "values", "state": {"active_node": "planner"}}
            yield {"type": "end"}

        with patch("api.playground_routes.stream_graph_impl", slow_stream), \
             patch("utils.server_config.ELICIT_KEEPALIVE_INTERVAL_S", 0.05):
            res = await client.post(
                "/api/playground/session_gated/stream_goap",
                json={"message": "hello"},
            )

        assert res.status_code == 200
        assert ": keepalive" in res.text
        events = _sse_events(res.text)
        assert events[-1] == {"type": "end"}


class TestPlaygroundChatLLM:
    @pytest.mark.asyncio
    async def test_chat_llm_missing_messages(self, client):
        res = await client.post("/api/playground/session_gated/chat_llm", json={"messages": []})
        assert res.status_code == 400
        assert res.json() == {"error": "missing_messages"}

    @pytest.mark.asyncio
    async def test_chat_llm_invalid_json(self, client):
        res = await client.post(
            "/api/playground/session_gated/chat_llm",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code == 400
        assert res.json() == {"error": "invalid_json"}

    @pytest.mark.asyncio
    async def test_chat_llm_streams_tokens(self, client):
        async def fake_astream(_msgs):
            yield SimpleNamespace(content="Hel")
            yield SimpleNamespace(content="lo")
            # Anthropic-style block list chunk
            yield SimpleNamespace(content=[{"type": "text", "text": "!"}, {"type": "tool_use"}])

        fake_llm = SimpleNamespace(astream=fake_astream)
        with patch("core.llm_config_service.get_graph_llm", AsyncMock(return_value=fake_llm)):
            res = await client.post(
                "/api/playground/session_gated/chat_llm",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
        assert res.status_code == 200
        events = _sse_events(res.text)
        tokens = [e for e in events if e["type"] == "token"]
        assert [t["text"] for t in tokens] == ["Hel", "lo", "!"]
        assert all(t["node"] == "llm" for t in tokens)
        assert events[-1] == {"type": "end"}

    @pytest.mark.asyncio
    async def test_chat_llm_error_inside_stream(self, client):
        with patch(
            "core.llm_config_service.get_graph_llm",
            AsyncMock(side_effect=RuntimeError("no api key")),
        ):
            res = await client.post(
                "/api/playground/session_gated/chat_llm",
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
        assert res.status_code == 200
        events = _sse_events(res.text)
        assert events[-1]["type"] == "error"
        assert "no api key" in events[-1]["message"]


class TestPlaygroundTools:
    @pytest.mark.asyncio
    async def test_tools_lists_schemas_with_plugin_mapping(self, client):
        fake_tools = [
            SimpleNamespace(
                name="create_record",
                description="Create a record",
                tags={"whiskers"},
                parameters={"type": "object", "properties": {"name": {"type": "string"}}},
                output_schema={"type": "object"},
            ),
            SimpleNamespace(
                name="orphan_tool",
                description="",
                tags=set(),
                parameters={},
                output_schema=None,
            ),
        ]
        fake_record = SimpleNamespace(id="fake_plugin", capabilities=["create_record"])
        fake_registry = SimpleNamespace(get_all=AsyncMock(return_value=[fake_record]))
        with patch("api.playground_routes.mcp.list_tools", AsyncMock(return_value=fake_tools)), \
             patch("api.playground_routes._DB_AVAILABLE", True), \
             patch("db_layer.plugin_registry_store.DBPluginRegistry", return_value=fake_registry):
            res = await client.get("/api/playground/session_gated/tools")
        assert res.status_code == 200
        tools = {t["name"]: t for t in res.json()["tools"]}
        assert tools["create_record"]["plugin"] == "fake_plugin"
        assert tools["create_record"]["input_schema"]["properties"]["name"] == {"type": "string"}
        assert tools["orphan_tool"]["plugin"] == "core"
        assert tools["orphan_tool"]["output_schema"] == {}


class TestPlaygroundInvoke:
    @pytest.mark.asyncio
    async def test_invoke_missing_tool(self, client):
        res = await client.post("/api/playground/session_gated/tools/invoke", json={"arguments": {}})
        assert res.status_code == 400
        assert res.json() == {"error": "missing_tool"}

    @pytest.mark.asyncio
    async def test_invoke_arguments_must_be_object(self, client):
        res = await client.post(
            "/api/playground/session_gated/tools/invoke", json={"tool": "x", "arguments": [1]}
        )
        assert res.status_code == 400
        assert res.json() == {"error": "arguments_must_be_object"}

    @pytest.mark.asyncio
    async def test_invoke_success(self, client):
        class FakeBlock:
            def model_dump(self, mode="json"):
                return {"type": "text", "text": "done"}

        fake_result = SimpleNamespace(
            structured_content={"id": 1}, content=[FakeBlock()]
        )
        with patch("api.playground_routes.mcp.call_tool", AsyncMock(return_value=fake_result)), \
             patch("api.playground_routes._resolve_principal_role", AsyncMock(return_value="admin")):
            res = await client.post(
                "/api/playground/session_gated/tools/invoke",
                json={"tool": "create_record", "arguments": {"name": "x"}},
            )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["tool"] == "create_record"
        assert body["structured_content"] == {"id": 1}
        assert body["content"] == [{"type": "text", "text": "done"}]
        assert isinstance(body["ms"], int)
        assert body["error"] is None

    @pytest.mark.asyncio
    async def test_invoke_tool_error_returned_as_data(self, client):
        from fastmcp.exceptions import ToolError

        with patch(
            "api.playground_routes.mcp.call_tool",
            AsyncMock(side_effect=ToolError("boom")),
        ):
            res = await client.post(
                "/api/playground/session_gated/tools/invoke", json={"tool": "bad_tool", "arguments": {}}
            )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is False
        assert body["error_type"] == "ToolError"
        assert "boom" in body["error"]


class TestPlaygroundChatSessions:
    @pytest.mark.asyncio
    @patch("api.playground_routes._db_available", return_value=False)
    async def test_sessions_db_unavailable(self, mock_db, client):
        res = await client.post("/api/playground/session_gated/chat/sessions", json={"title": "new"})
        assert res.status_code == 503
        
        res = await client.get("/api/playground/session_gated/chat/sessions")
        assert res.status_code == 200
        assert res.json() == {"sessions": []}

    @pytest.mark.asyncio
    @patch("api.playground_routes._db_available", return_value=True)
    async def test_create_session_success(self, mock_db, client):
        with patch("db_layer.chat_store.create_session", AsyncMock(return_value="sess-id-123")):
            res = await client.post("/api/playground/session_gated/chat/sessions", json={"title": "My Test Chat"})
        assert res.status_code == 200
        assert res.json() == {"id": "sess-id-123", "title": "My Test Chat"}

    @pytest.mark.asyncio
    @patch("api.playground_routes._db_available", return_value=True)
    async def test_list_sessions_success(self, mock_db, client):
        dummy_sessions = [{"id": "s1", "title": "s1 title", "created_at": None, "updated_at": None}]
        with patch("db_layer.chat_store.list_sessions", AsyncMock(return_value=dummy_sessions)):
            res = await client.get("/api/playground/session_gated/chat/sessions")
        assert res.status_code == 200
        assert res.json() == {"sessions": dummy_sessions}

    @pytest.mark.asyncio
    @patch("api.playground_routes._db_available", return_value=True)
    async def test_get_session_success(self, mock_db, client):
        dummy_session = {"id": "s1", "title": "s1 title", "messages": []}
        with patch("db_layer.chat_store.get_session", AsyncMock(return_value=dummy_session)):
            res = await client.get("/api/playground/session_gated/chat/sessions/s1")
        assert res.status_code == 200
        assert res.json() == dummy_session

    @pytest.mark.asyncio
    @patch("api.playground_routes._db_available", return_value=True)
    async def test_append_message_success(self, mock_db, client):
        with patch("db_layer.chat_store.append_message", AsyncMock(return_value="msg-id-123")), \
             patch("db_layer.chat_store.touch_session", AsyncMock()):
            res = await client.post(
                "/api/playground/session_gated/chat/sessions/s1/messages",
                json={"role": "user", "content": "hello", "engine": "agent"}
            )
        assert res.status_code == 200
        assert res.json() == {"id": "msg-id-123"}

