"""Phase 6c — public portfolio asset streaming route unit tests.

Presigned URLs can't back this (internal MinIO hostname, TTL, private
bucket), so the route streams bytes directly. The kind allowlist inside
resolve_public_asset is the load-bearing security check: without it this
route becomes a public read surface over the entire GOAP offload bucket.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.responses import JSONResponse, Response

from plugins.portfolio_plugin.routes import portfolio_asset


def _request(short_id: str) -> MagicMock:
    req = MagicMock()
    req.path_params = {"short_id": short_id}
    return req


@pytest.mark.asyncio
async def test_200_for_allowlisted_asset():
    meta = {"content_type": "image/png", "kind": "portfolio_asset"}
    body = b"\x89PNG\r\n\x1a\n"
    with patch(
        "plugins.portfolio_plugin.render.asset_store.resolve_public_asset",
        new_callable=AsyncMock,
        return_value=(meta, body),
    ):
        response = await portfolio_asset(_request("valid_short_id_1"))

    assert isinstance(response, Response)
    assert response.status_code == 200
    assert response.body == body
    assert response.media_type == "image/png"


@pytest.mark.asyncio
async def test_immutable_cache_and_acao_headers_present():
    meta = {"content_type": "image/png", "kind": "portfolio_asset"}
    with patch(
        "plugins.portfolio_plugin.render.asset_store.resolve_public_asset",
        new_callable=AsyncMock,
        return_value=(meta, b"bytes"),
    ):
        response = await portfolio_asset(_request("valid_short_id_1"))

    assert response.headers.get("Cache-Control") == "public, max-age=31536000, immutable"
    assert response.headers.get("Access-Control-Allow-Origin") == "*"


@pytest.mark.asyncio
async def test_bad_short_id_rejected_before_db_lookup():
    """Regex validation must reject before any store call -- mirrors
    portfolio_job_layout's JOB_ID_RE gate."""
    with patch(
        "plugins.portfolio_plugin.render.asset_store.resolve_public_asset",
        new_callable=AsyncMock,
    ) as mock_resolve:
        response = await portfolio_asset(_request("../etc/passwd"))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    mock_resolve.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_short_id_returns_404():
    with patch(
        "plugins.portfolio_plugin.render.asset_store.resolve_public_asset",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = await portfolio_asset(_request("valid_but_unknown"))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_store_error_returns_503():
    with patch(
        "plugins.portfolio_plugin.render.asset_store.resolve_public_asset",
        new_callable=AsyncMock,
        side_effect=RuntimeError("minio down"),
    ):
        response = await portfolio_asset(_request("valid_short_id_1"))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_non_allowlisted_kind_never_reaches_the_route_as_a_hit():
    """The load-bearing check: resolve_public_asset itself refuses a non-
    portfolio_asset kind (e.g. a GOAP offload artifact) and returns None --
    the route must surface that as 404, never leak the bytes."""
    from db_layer.artifact_link_store import get_artifact_link_by_short_id  # noqa: F401
    from plugins.portfolio_plugin.render.asset_store import resolve_public_asset

    with patch(
        "db_layer.artifact_link_store.get_artifact_link_by_short_id",
        new_callable=AsyncMock,
        return_value={
            "bucket": "whiskers-artifacts",
            "object_key": "1/session/log-abc123.md",
            "content_type": "text/markdown",
            "kind": "log",  # a real GOAP-offload kind, NOT portfolio_asset
        },
    ), patch(
        "core.artifact_store.minio_client.get_object_bytes"
    ) as mock_get_bytes:
        result = await resolve_public_asset("some_valid_id_1", tenant_id=1)

    assert result is None
    mock_get_bytes.assert_not_called()  # never even attempts to read the bytes

    with patch(
        "plugins.portfolio_plugin.render.asset_store.resolve_public_asset",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = await portfolio_asset(_request("some_valid_id_1"))
    assert isinstance(response, JSONResponse)
    assert response.status_code == 404
