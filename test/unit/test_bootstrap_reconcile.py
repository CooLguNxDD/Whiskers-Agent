"""Unit tests for Phase 2b plugin registry reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.bootstrap.phases import phase_plugin_reconcile, PHASES


@dataclass
class _FakeRecord:
    id: str
    meta: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    version: str = "1.0.0"


@dataclass
class _FakeSpec:
    name: str
    package: str = "plugins.x"


class _FakeCtx:
    db_available = True


class _FakeRegistry:
    def __init__(self, records: list[_FakeRecord]):
        self._records = {r.id: r for r in records}
        self.mark_stale = AsyncMock(side_effect=self._mark)
        self.clear_stale = AsyncMock(side_effect=self._clear)
        self.get_all = AsyncMock(return_value=list(self._records.values()))

    async def _mark(self, plugin_id: str) -> bool:
        r = self._records[plugin_id]
        if r.meta.get("stale"):
            return False
        r.meta["stale"] = True
        r.meta["stale_since"] = "now"
        return True

    async def _clear(self, plugin_id: str) -> bool:
        r = self._records[plugin_id]
        if not r.meta.get("stale"):
            return False
        r.meta.pop("stale", None)
        r.meta.pop("stale_since", None)
        return True


def test_phase_registered_between_proxy_mount_and_preseed():
    names = [p.name for p in PHASES]
    assert "Phase 2: Upstream Proxy Mounting & Tool Routing" in names
    assert "Phase 2b: Plugin Registry Reconciliation" in names
    assert "scope-registry-preseed" in names
    i2 = names.index("Phase 2: Upstream Proxy Mounting & Tool Routing")
    i2b = names.index("Phase 2b: Plugin Registry Reconciliation")
    ipre = names.index("scope-registry-preseed")
    assert i2 < i2b < ipre


@pytest.mark.asyncio
async def test_reconcile_marks_ghost_clears_recovered_preserves_active():
    ghost = _FakeRecord(id="gone_plugin", meta={}, is_active=True)
    recovered = _FakeRecord(
        id="live_plugin",
        meta={"stale": True, "stale_since": "t0"},
        is_active=False,
    )
    already = _FakeRecord(
        id="still_gone",
        meta={"stale": True, "stale_since": "t0"},
        is_active=True,
    )
    proxy_ok = _FakeRecord(id="proxy_ok", meta={}, is_active=True)
    proxy_ghost = _FakeRecord(id="proxy_missing", meta={}, is_active=True)

    fake = _FakeRegistry([ghost, recovered, already, proxy_ok, proxy_ghost])

    with (
        patch("db_layer.plugin_registry_store.DBPluginRegistry", return_value=fake),
        patch("core.proxy.proxy_manager.ProxyManager.list_proxies", return_value=[{"name": "ok"}]),
        patch(
            "core.plugin_loader.resolver.build_specs",
            return_value=([_FakeSpec(name="live_plugin")], []),
        ),
        patch(
            "utils.config_registry.PLUGIN_CONFIG_PATH",
            "/tmp/plugin_config.json",
        ),
        patch("builtins.open", create=True) as mock_open,
    ):
        mock_open.return_value.__enter__ = MagicMock(
            return_value=MagicMock(
                **{
                    "read.return_value": '{"plugins": ["plugins.live"]}',
                }
            )
        )
        # json.load path: open returns file-like; patch json.load instead
        with patch("json.load", return_value={"plugins": ["plugins.live"]}):
            await phase_plugin_reconcile(_FakeCtx())

    # ghost marked
    fake.mark_stale.assert_any_await("gone_plugin")
    fake.mark_stale.assert_any_await("proxy_missing")
    # already-stale not rewritten
    assert all(c.args[0] != "still_gone" for c in fake.mark_stale.await_args_list)
    # recovered cleared
    fake.clear_stale.assert_any_await("live_plugin")
    # is_active untouched on all records
    assert ghost.is_active is True
    assert recovered.is_active is False
    assert already.is_active is True
    assert proxy_ok.is_active is True


@pytest.mark.asyncio
async def test_reconcile_skips_when_db_unavailable():
    class Ctx:
        db_available = False

    with patch("db_layer.plugin_registry_store.DBPluginRegistry") as ctor:
        await phase_plugin_reconcile(Ctx())
        ctor.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_skips_when_plugin_config_unreadable():
    """Config read failure must not mark every live plugin stale (empty packages)."""
    live = _FakeRecord(id="live_plugin", meta={}, is_active=True)
    fake = _FakeRegistry([live])

    with (
        patch("db_layer.plugin_registry_store.DBPluginRegistry", return_value=fake),
        patch(
            "utils.config_registry.PLUGIN_CONFIG_PATH",
            "/tmp/missing_plugin_config.json",
        ),
        patch("builtins.open", side_effect=OSError("no such file")),
    ):
        await phase_plugin_reconcile(_FakeCtx())

    fake.mark_stale.assert_not_awaited()
    fake.clear_stale.assert_not_awaited()

@pytest.mark.asyncio
async def test_reconcile_skips_proxy_records_on_lookup_failure():
    proxy_record = _FakeRecord(id="proxy_test", meta={}, is_active=True)
    fake_registry = _FakeRegistry([proxy_record])

    with patch("db_layer.plugin_registry_store.DBPluginRegistry", return_value=fake_registry), \
         patch("core.plugin_loader.resolver.build_specs", return_value=([], [])), \
         patch("utils.config_registry.PLUGIN_CONFIG_PATH", "/tmp/plugin_config.json"), \
         patch("builtins.open", create=True) as mock_open, \
         patch("json.load", return_value={"plugins": []}), \
         patch("core.proxy.proxy_manager.ProxyManager.list_proxies", new_callable=AsyncMock) as mock_list_proxies:

        mock_list_proxies.side_effect = Exception("db connection failed")

        await phase_plugin_reconcile(_FakeCtx())

        fake_registry.mark_stale.assert_not_awaited()
        fake_registry.clear_stale.assert_not_awaited()
