"""Unit tests for session-gated artifact REST routes."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.responses import JSONResponse, RedirectResponse

from core.artifact_store.routes import (
    api_artifacts_delete,
    api_artifacts_download,
    api_artifacts_get,
    api_artifacts_list,
)


def _req(path_params=None, query_params=None):
    r = MagicMock()
    r.path_params = path_params or {}
    r.query_params = query_params or {}
    return r


@pytest.mark.asyncio
async def test_list_artifacts_ok():
    rows = [
        {
            "short_id": "art_diff_001",
            "kind": "diff",
            "bytes": 100,
            "content_type": "text/markdown",
            "session_id": "s1",
            "source_path": "p",
            "created_at": "2026-01-01T00:00:00+00:00",
            "object_key": "secret",
        }
    ]
    with patch(
        "db_layer.artifact_link_store.list_artifact_links",
        new_callable=AsyncMock,
        return_value=rows,
    ):
        resp = await api_artifacts_list(_req(query_params={"session_id": "s1"}))
    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["count"] == 1
    assert body["artifacts"][0]["short_id"] == "art_diff_001"
    assert "object_key" not in body["artifacts"][0]


@pytest.mark.asyncio
async def test_get_invalid_short_id():
    resp = await api_artifacts_get(_req(path_params={"short_id": "BAD!"}))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_not_found():
    with patch(
        "db_layer.artifact_link_store.get_artifact_link_by_short_id",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await api_artifacts_get(_req(path_params={"short_id": "art_missing_001"}))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_meta_ok():
    row = {
        "short_id": "art_diff_001",
        "kind": "diff",
        "bytes": 50,
        "content_type": "text/markdown",
        "session_id": "s",
        "source_path": "x",
        "created_at": None,
    }
    with patch(
        "db_layer.artifact_link_store.get_artifact_link_by_short_id",
        new_callable=AsyncMock,
        return_value=row,
    ):
        resp = await api_artifacts_get(_req(path_params={"short_id": "art_diff_001"}))
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["short_id"] == "art_diff_001"
    assert body["console_path"].endswith("art_diff_001")


@pytest.mark.asyncio
async def test_download_302():
    with patch(
        "core.artifact_store.routes.mint_download_url",
        new_callable=AsyncMock,
        return_value="https://minio.example/presigned",
    ):
        resp = await api_artifacts_download(_req(path_params={"short_id": "art_diff_001"}))
    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://minio.example/presigned"


@pytest.mark.asyncio
async def test_download_not_found():
    with patch(
        "core.artifact_store.routes.mint_download_url",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await api_artifacts_download(_req(path_params={"short_id": "art_gone_001"}))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_ok():
    row = {
        "short_id": "art_diff_001",
        "bucket": "b",
        "object_key": "k",
    }
    with (
        patch(
            "db_layer.artifact_link_store.get_artifact_link_by_short_id",
            new_callable=AsyncMock,
            return_value=row,
        ),
        patch(
            "db_layer.artifact_link_store.delete_artifact_link",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("core.artifact_store.minio_client.remove_object") as rem,
    ):
        resp = await api_artifacts_delete(_req(path_params={"short_id": "art_diff_001"}))
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["deleted"] is True
    rem.assert_called_once_with("b", "k")
