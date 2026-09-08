"""Phase 6c — raster.py + asset_store.py unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from plugins.portfolio_plugin.render import raster
from plugins.portfolio_plugin.render.asset_store import (
    is_valid_asset_short_id,
    store_png_asset,
)


def test_is_valid_asset_short_id():
    assert is_valid_asset_short_id("asset_portfolio_abc123") is True
    assert is_valid_asset_short_id("") is False
    assert is_valid_asset_short_id("../etc/passwd") is False
    assert is_valid_asset_short_id("has spaces") is False
    assert is_valid_asset_short_id("UPPERCASE") is False


def test_svg_to_png_bytes_fails_open_when_cairosvg_unavailable():
    """Before the Docker image is rebuilt with cairosvg, calls must degrade
    to None (no baked raster available) rather than crash the caller."""
    raster._cairosvg_available = None
    with patch.dict("sys.modules", {"cairosvg": None}):
        assert raster.svg_to_png_bytes("<svg/>") is None
    raster._cairosvg_available = None  # reset cache for other tests


def test_svg_to_png_bytes_empty_input_returns_none():
    assert raster.svg_to_png_bytes("") is None


def test_svg_to_png_bytes_uses_cairosvg_when_available(monkeypatch):
    class _FakeCairoSvg:
        @staticmethod
        def svg2png(*, bytestring, output_width=None):
            assert b"<svg" in bytestring
            return b"\x89PNG\r\n\x1a\n"

    raster._cairosvg_available = True
    with patch.dict("sys.modules", {"cairosvg": _FakeCairoSvg()}):
        result = raster.svg_to_png_bytes("<svg></svg>")
    assert result == b"\x89PNG\r\n\x1a\n"
    raster._cairosvg_available = None


@pytest.mark.asyncio
async def test_store_png_asset_empty_bytes_returns_none():
    assert await store_png_asset(b"") is None


@pytest.mark.asyncio
async def test_store_png_asset_uploads_and_mints_link():
    with patch(
        "core.artifact_store.minio_client.put_object_bytes"
    ) as mock_put, patch(
        "db_layer.artifact_link_store.artifact_link_short_id_exists",
        new_callable=AsyncMock,
        return_value=False,
    ), patch(
        "db_layer.artifact_link_store.create_artifact_link",
        new_callable=AsyncMock,
        return_value={"short_id": "asset_portfolio_xyz", "kind": "portfolio_asset"},
    ) as mock_create:
        result = await store_png_asset(b"\x89PNG\r\n\x1a\n", tenant_id=1)

    assert result is not None
    assert result["kind"] == "portfolio_asset"
    mock_put.assert_called_once()
    args, kwargs = mock_create.call_args
    assert mock_create.call_args.kwargs["kind"] == "portfolio_asset"
    assert mock_create.call_args.kwargs["tenant_id"] == 1
    assert mock_create.call_args.kwargs["content_type"] == "image/png"


@pytest.mark.asyncio
async def test_store_png_asset_upload_failure_fails_open():
    with patch(
        "core.artifact_store.minio_client.put_object_bytes",
        side_effect=RuntimeError("minio unreachable"),
    ):
        result = await store_png_asset(b"\x89PNG\r\n\x1a\n", tenant_id=1)
    assert result is None


@pytest.mark.asyncio
async def test_store_png_asset_short_id_collision_gives_up():
    with patch(
        "core.artifact_store.minio_client.put_object_bytes"
    ), patch(
        "db_layer.artifact_link_store.artifact_link_short_id_exists",
        new_callable=AsyncMock,
        return_value=True,  # every candidate collides
    ), patch(
        "db_layer.artifact_link_store.create_artifact_link",
        new_callable=AsyncMock,
    ) as mock_create:
        result = await store_png_asset(b"\x89PNG\r\n\x1a\n", tenant_id=1)

    assert result is None
    mock_create.assert_not_called()
