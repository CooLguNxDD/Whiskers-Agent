"""Unit tests for plugin stale payload, DELETE, and prune-stale endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db_layer.plugin_registry_store import PluginRecord, PluginMetaKey
from api.plugin_routes import (
    _build_plugin_payload,
    delete_plugin,
    list_plugins,
    prune_stale_plugins,
)


def _record(
    plugin_id: str,
    *,
    stale: bool = False,
    is_active: bool = True,
    content_hash: str | None = "sha256:abc",
) -> PluginRecord:
    meta = {}
    if stale:
        meta[PluginMetaKey.STALE.value] = True
        meta[PluginMetaKey.STALE_SINCE.value] = "t0"
    return PluginRecord(
        id=plugin_id,
        display_name=plugin_id,
        version="1.0.0",
        meta=meta,
        is_active=is_active,
        registered_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        content_hash=content_hash,
    )


def _request(path: str = "/api/plugins/x", path_params: dict | None = None):
    """Minimal request stand-in (Starlette Request.path_params is read-only)."""
    return SimpleNamespace(path_params=path_params or {}, url=path)


def test_build_payload_includes_stale_and_content_hash():
    plugin = SimpleNamespace(name="ghost", version="0.1.0", tier=1)
    record = _record("ghost", stale=True, content_hash="sha256:dead")
    payload = _build_plugin_payload(plugin, record, {})
    assert payload["stale"] is True
    assert payload["content_hash"] == "sha256:dead"
    assert payload["enabled"] is True  # is_active preserved; not derived from stale


def test_build_payload_disabled_not_stale():
    plugin = SimpleNamespace(name="off", version="0.1.0", tier=1)
    record = _record("off", stale=False, is_active=False, content_hash=None)
    payload = _build_plugin_payload(plugin, record, {})
    assert payload["stale"] is False
    assert payload["enabled"] is False
    assert payload["content_hash"] is None


@pytest.mark.asyncio
async def test_list_plugins_returns_stale_true_for_ghost():
    ghost = _record("ghost_plugin", stale=True)
    live = SimpleNamespace(name="live_plugin", version="1.0.0", tier=1)

    class _Lifecycle:
        _plugins = [live]
        _plugin_id_map = {"live_plugin": live}

    class _Registry:
        lifecycle = _Lifecycle()
        system_tier = 1

    with (
        patch("api.plugin_routes.registry.get_registry", return_value=_Registry()),
        patch(
            "api.plugin_routes.helpers._db_registry.get_all",
            new=AsyncMock(return_value=[ghost, _record("live_plugin")]),
        ),
        patch("api.plugin_routes.registry.oauth_relay", None),
    ):
        resp = await list_plugins(_request("/api/plugins"))

    body = resp.body
    import json

    data = json.loads(body)
    by_id = {p["id"]: p for p in data["plugins"]}
    assert by_id["ghost_plugin"]["stale"] is True
    assert by_id["live_plugin"]["stale"] is False


@pytest.mark.asyncio
async def test_delete_409_when_live():
    live_plugin = SimpleNamespace(name="live_plugin")

    class _Lifecycle:
        _plugin_id_map = {"live_plugin": live_plugin}

    class _Registry:
        lifecycle = _Lifecycle()

    with (
        patch(
            "api.plugin_routes.helpers._db_registry.get",
            new=AsyncMock(return_value=_record("live_plugin", stale=True)),
        ),
        patch("api.plugin_routes.registry.get_registry", return_value=_Registry()),
    ):
        resp = await delete_plugin(
            _request("/api/plugins/live_plugin", {"plugin_id": "live_plugin"})
        )

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_delete_removes_stale_row():
    with (
        patch(
            "api.plugin_routes.helpers._db_registry.get",
            new=AsyncMock(return_value=_record("ghost", stale=True)),
        ),
        patch("api.plugin_routes.registry.get_registry", side_effect=RuntimeError("no reg")),
        patch(
            "api.plugin_routes.helpers._db_registry.delete_plugin",
            new=AsyncMock(return_value=True),
        ) as del_mock,
    ):
        resp = await delete_plugin(
            _request("/api/plugins/ghost", {"plugin_id": "ghost"})
        )

    assert resp.status_code == 200
    del_mock.assert_awaited_once_with("ghost")
    import json

    assert json.loads(resp.body) == {"id": "ghost", "deleted": True}


@pytest.mark.asyncio
async def test_delete_404_missing():
    with patch(
        "api.plugin_routes.helpers._db_registry.get",
        new=AsyncMock(return_value=None),
    ):
        resp = await delete_plugin(
            _request("/api/plugins/missing", {"plugin_id": "missing"})
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_prune_stale_count():
    records = [
        _record("stale_a", stale=True),
        _record("stale_b", stale=True),
        _record("fresh", stale=False),
        _record("live_stale", stale=True),
    ]
    live = SimpleNamespace(name="live_stale")

    class _Lifecycle:
        _plugin_id_map = {"live_stale": live}

    class _Registry:
        lifecycle = _Lifecycle()

    with (
        patch(
            "api.plugin_routes.helpers._db_registry.get_all",
            new=AsyncMock(return_value=records),
        ),
        patch("api.plugin_routes.registry.get_registry", return_value=_Registry()),
        patch(
            "api.plugin_routes.helpers._db_registry.delete_plugin",
            new=AsyncMock(return_value=True),
        ) as del_mock,
    ):
        resp = await prune_stale_plugins(_request("/api/plugins/prune-stale"))

    import json

    data = json.loads(resp.body)
    assert data["pruned"] == 2
    deleted_ids = {c.args[0] for c in del_mock.await_args_list}
    assert deleted_ids == {"stale_a", "stale_b"}
