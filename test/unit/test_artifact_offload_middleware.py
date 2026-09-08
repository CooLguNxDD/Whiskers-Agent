"""Unit tests for core/context/artifact_offload_middleware.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
import mcp.types as mt
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from core.context.artifact_offload_middleware import ArtifactOffloadMiddleware
from core.artifact_store.store import is_offload_marker


def _ctx(name: str, arguments: dict | None = None) -> MiddlewareContext:
    return MiddlewareContext(
        message=mt.CallToolRequestParams(name=name, arguments=arguments or {})
    )


def _mock_minio(monkeypatch, *, enabled: bool = True, available: bool = True):
    monkeypatch.setattr("utils.server_config.ARTIFACT_OFFLOAD_ENABLED", enabled)
    monkeypatch.setattr("core.artifact_store.minio_client.minio_available", lambda: available)


@pytest.fixture
def fake_offload_text():
    counter = 0

    async def _fake_offload(content, **kwargs):
        nonlocal counter
        counter += 1
        short_id = f"art_test_{counter:03d}"
        path = kwargs.get("source_path") or "data"
        return {
            "kind": kwargs.get("kind", "text"),
            "short_id": short_id,
            "bytes": len(content.encode("utf-8")),
            "content_type": "text/markdown",
            "session_id": kwargs.get("session_id"),
            "path": path,
            "console_path": f"/api/artifacts/session_gated/{short_id}",
            "object_key": f"1/s/{short_id}.txt",
        }

    with patch("core.artifact_store.store.offload_text", side_effect=_fake_offload) as mocked:
        yield mocked


@pytest.mark.asyncio
async def test_dict_structured_with_large_leaf(monkeypatch, fake_offload_text):
    """Structured dict with large leaf replaces leaf with marker and formats content JSON."""
    _mock_minio(monkeypatch)
    big = "patch line\n" * 300  # ~3300 chars > 2000
    small = "small note"

    async def call_next(ctx):
        return ToolResult(
            content=[],
            structured_content={"patch": big, "note": small},
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("get_activity"), call_next)

    assert is_offload_marker(res.structured_content["patch"])
    assert res.structured_content["note"] == small
    assert len(res.content) == 1
    assert isinstance(res.content[0], mt.TextContent)
    parsed = json.loads(res.content[0].text)
    assert is_offload_marker(parsed["patch"])
    # Real store refs carry short_id (not fabricated half-refs)
    assert fake_offload_text.call_count >= 1
    call_kwargs = fake_offload_text.call_args.kwargs
    assert "session_id" in call_kwargs or fake_offload_text.call_args[1].get("session_id") is None


@pytest.mark.asyncio
async def test_structured_none_single_large_plain_text(monkeypatch, fake_offload_text):
    """structured_content=None with single large plain TextContent offloads whole string."""
    _mock_minio(monkeypatch)
    big_file = "line of code in plain file\n" * 300

    async def call_next(ctx):
        return ToolResult(
            content=[mt.TextContent(type="text", text=big_file)],
            structured_content=None,
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("github-andrew_get_file_contents"), call_next)

    assert len(res.content) == 1
    assert isinstance(res.content[0], mt.TextContent)
    assert is_offload_marker(res.content[0].text)
    assert "art_test_" in res.content[0].text
    assert "--- preview ---" in res.content[0].text
    # short_id present in marker
    assert "short_id=art_test_" in res.content[0].text


@pytest.mark.asyncio
async def test_session_id_resolved_from_arguments(monkeypatch, fake_offload_text):
    """Shared helper receives session_id resolved from context.message.arguments."""
    _mock_minio(monkeypatch)
    big = "session-aware body\n" * 300

    async def call_next(ctx):
        return ToolResult(
            content=[mt.TextContent(type="text", text=big)],
            structured_content=None,
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(
        _ctx("github-andrew_get_file_contents", {"session_id": "sess-abc-123"}),
        call_next,
    )

    assert is_offload_marker(res.content[0].text)
    assert fake_offload_text.call_count >= 1
    # offload_text is called with session_id from request args
    found_session = False
    for call in fake_offload_text.call_args_list:
        kwargs = call.kwargs if call.kwargs else {}
        if kwargs.get("session_id") == "sess-abc-123":
            found_session = True
            break
    assert found_session, "expected session_id=sess-abc-123 on offload_text call"


@pytest.mark.asyncio
async def test_small_text_under_threshold(monkeypatch, fake_offload_text):
    """Small text under threshold is returned completely unchanged."""
    _mock_minio(monkeypatch)
    small = "hello world"

    async def call_next(ctx):
        return ToolResult(
            content=[mt.TextContent(type="text", text=small)],
            structured_content=None,
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("get_greeting"), call_next)

    assert res.content[0].text == small
    assert fake_offload_text.call_count == 0


@pytest.mark.asyncio
async def test_large_json_text_with_big_nested_patch(monkeypatch, fake_offload_text):
    """Large JSON text with big nested patch string runs leaf walk and sets structured override."""
    _mock_minio(monkeypatch)
    big = "nested patch line\n" * 300
    json_str = json.dumps({"patch": big, "meta": {"author": "andrew"}})

    async def call_next(ctx):
        return ToolResult(
            content=[mt.TextContent(type="text", text=json_str)],
            structured_content=None,
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("github-andrew_get_commit"), call_next)

    assert len(res.content) == 1
    parsed = json.loads(res.content[0].text)
    assert is_offload_marker(parsed["patch"])
    assert parsed["meta"]["author"] == "andrew"
    assert res.structured_content is not None
    assert is_offload_marker(res.structured_content["patch"])


@pytest.mark.asyncio
async def test_large_json_with_small_leaves_fallback_whole_string(monkeypatch, fake_offload_text):
    """Large JSON with only small leaves but total size >= min_bytes falls back to whole string offload."""
    _mock_minio(monkeypatch)
    items = [{"id": i, "name": f"item_{i}"} for i in range(250)]
    json_str = json.dumps(items)
    assert len(json_str) > 2000

    async def call_next(ctx):
        return ToolResult(
            content=[mt.TextContent(type="text", text=json_str)],
            structured_content=None,
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("github-andrew_list_commits"), call_next)

    assert len(res.content) == 1
    assert is_offload_marker(res.content[0].text)
    assert "short_id=art_test_" in res.content[0].text


@pytest.mark.asyncio
async def test_multi_block_content_one_large_one_small(monkeypatch, fake_offload_text):
    """Multi-block content only rewrites the large block."""
    _mock_minio(monkeypatch)
    big = "big text block\n" * 300
    small = "small text block"

    async def call_next(ctx):
        return ToolResult(
            content=[
                mt.TextContent(type="text", text=big),
                mt.TextContent(type="text", text=small),
            ],
            structured_content=None,
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("custom_tool"), call_next)

    assert len(res.content) == 2
    assert is_offload_marker(res.content[0].text)
    assert res.content[1].text == small


@pytest.mark.asyncio
async def test_multi_block_plus_structured_preserves_blocks(monkeypatch, fake_offload_text):
    """Multi-block content + structured dict: blocks preserved (not replaced by JSON mirror)."""
    _mock_minio(monkeypatch)
    big = "structured big leaf\n" * 300
    block_a = "content block A\n" * 300
    block_b = "content block B small"

    async def call_next(ctx):
        return ToolResult(
            content=[
                mt.TextContent(type="text", text=block_a),
                mt.TextContent(type="text", text=block_b),
            ],
            structured_content={"patch": big, "note": "ok"},
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("multi_with_structured"), call_next)

    # Two content blocks preserved (not collapsed to single JSON mirror)
    assert len(res.content) == 2
    assert isinstance(res.content[0], mt.TextContent)
    assert isinstance(res.content[1], mt.TextContent)
    # Structured leaf was offloaded
    assert is_offload_marker(res.structured_content["patch"])
    assert res.structured_content["note"] == "ok"
    # Large content block also offloaded (remaining budget)
    assert is_offload_marker(res.content[0].text)
    assert res.content[1].text == block_b


@pytest.mark.asyncio
async def test_already_offloaded_marker_not_reoffloaded(monkeypatch, fake_offload_text):
    """Already-offloaded marker does not trigger a second MinIO upload."""
    _mock_minio(monkeypatch)
    marker = "[offloaded: data short_id=art_diff_001 (5000 bytes)]\n--- preview ---\nline 1\n"

    async def call_next(ctx):
        return ToolResult(
            content=[mt.TextContent(type="text", text=marker)],
            structured_content=None,
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("fetch_artifact"), call_next)

    assert res.content[0].text == marker
    assert fake_offload_text.call_count == 0


@pytest.mark.asyncio
async def test_gateway_always_visible_excluded(monkeypatch, fake_offload_text):
    """GATEWAY_ALWAYS_VISIBLE tools like run_graph are untouched."""
    _mock_minio(monkeypatch)
    big = "summary text\n" * 300

    async def call_next(ctx):
        return ToolResult(
            content=[mt.TextContent(type="text", text=big)],
            structured_content={"summary": big},
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("run_graph"), call_next)

    assert res.content[0].text == big
    assert res.structured_content["summary"] == big
    assert fake_offload_text.call_count == 0


@pytest.mark.asyncio
async def test_minio_unavailable_passthrough(monkeypatch, fake_offload_text):
    """When MinIO is unavailable or offload disabled, result is passed through unchanged."""
    _mock_minio(monkeypatch, enabled=False, available=False)
    big = "big text\n" * 300

    async def call_next(ctx):
        return ToolResult(
            content=[mt.TextContent(type="text", text=big)],
            structured_content={"big": big},
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("get_data"), call_next)

    assert res.content[0].text == big
    assert res.structured_content["big"] == big
    assert fake_offload_text.call_count == 0


@pytest.mark.asyncio
async def test_embedded_text_resource_contents_offloaded(monkeypatch, fake_offload_text):
    """Embedded TextResourceContents large text is offloaded like TextContent."""
    _mock_minio(monkeypatch)
    big = "embedded resource file body\n" * 300

    async def call_next(ctx):
        return ToolResult(
            content=[
                mt.EmbeddedResource(
                    type="resource",
                    resource=mt.TextResourceContents(
                        uri="file:///workspace/large_file.py",
                        mimeType="text/x-python",
                        text=big,
                    ),
                )
            ],
            structured_content=None,
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("read_resource"), call_next)

    assert len(res.content) == 1
    assert isinstance(res.content[0], mt.EmbeddedResource)
    assert isinstance(res.content[0].resource, mt.TextResourceContents)
    assert is_offload_marker(res.content[0].resource.text)
    assert str(res.content[0].resource.uri) == "file:///workspace/large_file.py"
    assert "short_id=art_test_" in res.content[0].resource.text


@pytest.mark.asyncio
async def test_extract_large_artifacts_raises_fail_open(monkeypatch, fake_offload_text):
    """If extract_large_artifacts raises mid-pass, result is returned untouched."""
    _mock_minio(monkeypatch)
    big = "boom body\n" * 300

    async def call_next(ctx):
        return ToolResult(
            content=[mt.TextContent(type="text", text=big)],
            structured_content=None,
        )

    with patch(
        "core.artifact_store.store.extract_large_artifacts",
        new_callable=AsyncMock,
        side_effect=RuntimeError("minio blew up"),
    ):
        mw = ArtifactOffloadMiddleware()
        res = await mw.on_call_tool(_ctx("flaky_tool"), call_next)

    assert res.content[0].text == big
    assert not is_offload_marker(res.content[0].text)


@pytest.mark.asyncio
async def test_compose_with_response_shape_middleware_idempotent(
    monkeypatch, fake_offload_text
):
    """Already-shaped/marked result passes through ArtifactOffloadMiddleware idempotently."""
    _mock_minio(monkeypatch)
    marker = (
        "[offloaded: data short_id=art_shaped_001 (8000 bytes)]\n"
        "--- preview ---\nfirst lines of already shaped body\n"
    )

    async def call_next(ctx):
        # Simulates ResponseShapeMiddleware already having offloaded
        return ToolResult(
            content=[mt.TextContent(type="text", text=marker)],
            structured_content={"data": marker},
        )

    mw = ArtifactOffloadMiddleware()
    res = await mw.on_call_tool(_ctx("shaped_tool"), call_next)

    assert res.content[0].text == marker
    assert res.structured_content["data"] == marker
    assert fake_offload_text.call_count == 0
