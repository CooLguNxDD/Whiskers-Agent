"""Unit tests for core/artifact_store/tools MCP tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.artifact_store.tools import fetch_artifact, get_artifact, list_artifacts


@pytest.mark.asyncio
async def test_list_artifacts_ok():
    rows = [
        {
            "short_id": "art_diff_001",
            "kind": "diff",
            "bytes": 10,
            "content_type": "text/markdown",
            "session_id": "s",
            "source_path": "p",
            "created_at": None,
        }
    ]
    with patch(
        "db_layer.artifact_link_store.list_artifact_links",
        new_callable=AsyncMock,
        return_value=rows,
    ):
        out = await list_artifacts(session_id="s")
    assert out["status"] == "ok"
    assert out["count"] == 1
    assert out["artifacts"][0]["short_id"] == "art_diff_001"


@pytest.mark.asyncio
async def test_get_artifact_invalid_id():
    out = await get_artifact("BAD!")
    assert out["status"] == "error"
    assert out["message"] == "invalid_short_id"


@pytest.mark.asyncio
async def test_get_artifact_with_url():
    meta = {
        "short_id": "art_diff_001",
        "kind": "diff",
        "bytes": 10,
        "content_type": "text/markdown",
        "session_id": "s",
        "source_path": "p",
        "created_at": None,
    }
    with (
        patch(
            "core.artifact_store.tools.resolve_artifact_meta",
            new_callable=AsyncMock,
            return_value=meta,
        ),
        patch(
            "core.artifact_store.tools.mint_download_url",
            new_callable=AsyncMock,
            return_value="https://minio/x",
        ),
    ):
        out = await get_artifact("art_diff_001", include_url=True)
    assert out["status"] == "ok"
    assert out["download_url"] == "https://minio/x"


@pytest.mark.asyncio
async def test_fetch_artifact_truncates():
    meta = {
        "short_id": "art_log_001",
        "kind": "log",
        "bytes": 100,
        "content_type": "text/plain",
        "session_id": "s",
        "source_path": "p",
        "created_at": None,
        "bucket": "b",
        "object_key": "k",
    }
    body = b"ABCDEFGHIJ" * 20  # 200 bytes
    with patch(
        "core.artifact_store.tools.read_artifact_bytes",
        new_callable=AsyncMock,
        return_value=(meta, body),
    ):
        out = await fetch_artifact("art_log_001", max_chars=50)
    assert out["status"] == "ok"
    assert out["truncated"] is True
    assert len(out["content"]) == 50


@pytest.mark.asyncio
async def test_fetch_artifact_include_content_false_is_meta_only():
    """fetch_artifact(include_content=false) collapses get_artifact path."""
    meta = {
        "short_id": "art_diff_001",
        "kind": "diff",
        "bytes": 10,
        "content_type": "text/markdown",
        "session_id": "s",
        "source_path": "p",
        "created_at": None,
    }
    with (
        patch(
            "core.artifact_store.tools.resolve_artifact_meta",
            new_callable=AsyncMock,
            return_value=meta,
        ),
        patch(
            "core.artifact_store.tools.read_artifact_bytes",
            new_callable=AsyncMock,
        ) as read_body,
    ):
        out = await fetch_artifact("art_diff_001", include_content=False)
    assert out["status"] == "ok"
    assert out["short_id"] == "art_diff_001"
    assert "content" not in out
    read_body.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_artifact_not_found():
    with patch(
        "core.artifact_store.tools.read_artifact_bytes",
        new_callable=AsyncMock,
        return_value=None,
    ):
        out = await fetch_artifact("art_missing_001")
    assert out["status"] == "error"
    assert out["message"] == "not_found"
