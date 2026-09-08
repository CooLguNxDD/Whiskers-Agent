"""Loader must persist content_hash even when credential gate blocks load."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.plugin_loader.plugin_loader import _load_one_spec, _persist_spec_content_hash
from core.plugin_loader.resolver import PluginSpec
from db_layer.plugin_registry_store import PluginLoadError


def _spec(name: str = "demo_plugin", content_hash: str = "sha256:abc") -> PluginSpec:
    return PluginSpec(
        package=f"plugins.{name}",
        name=name,
        tier=1,
        requires=(),
        required_credentials=(),
        manifest_path=Path(f"plugins/{name}/manifest.json"),
        manifest={"name": name, "version": "1.2.3"},
        content_hash=content_hash,
    )


@pytest.mark.asyncio
async def test_persist_spec_content_hash_writes_when_changed():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(meta={}))
    db.set_content_hash = AsyncMock()
    await _persist_spec_content_hash(_spec(), db)
    db.set_content_hash.assert_awaited_once_with("demo_plugin", "sha256:abc", "1.2.3")


@pytest.mark.asyncio
async def test_persist_spec_content_hash_skips_when_unchanged():
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(meta={"content_hash": "sha256:abc"}))
    db.set_content_hash = AsyncMock()
    await _persist_spec_content_hash(_spec(), db)
    db.set_content_hash.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_one_spec_persists_hash_on_credential_block():
    """PluginLoadError after register must still write content_hash."""
    registry = MagicMock()
    registry.system_tier = 999
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(meta={}))
    db.set_content_hash = AsyncMock()
    db.get_skills = AsyncMock(return_value={})
    db.get_scopes = AsyncMock(return_value=([], ""))
    vault = MagicMock()

    blocked = AsyncMock(side_effect=PluginLoadError("missing credentials: X"))
    fake_pkg = SimpleNamespace(register=MagicMock())

    # Patch DB helpers before importlib — import_module is patched last so it
    # does not break lookup of core.plugin_loader.plugin_loader attributes.
    with patch(
        "core.plugin_loader.lifecycle_manager.PluginLifecycleManager.db_upsert_and_validate",
        blocked,
    ), patch(
        "core.plugin_loader.lifecycle_manager.PluginLifecycleManager.persist_spec_content_hash",
        new_callable=AsyncMock,
    ) as persist, patch(
        "core.plugin_loader.lifecycle_manager.load_plugin_config",
    ), patch(
        "importlib.import_module",
        return_value=fake_pkg,
    ):
        ok = await _load_one_spec(
            registry,
            _spec("job_search_plugin", "sha256:job"),
            db_registry=db,
            vault=vault,
            shortname_to_manifest_name={},
            manifests_by_shortname={},
        )

    assert ok is False
    blocked.assert_awaited_once()
    persist.assert_awaited_once()
    # content_hash persist runs before the early return
    assert persist.await_args.args[0].name == "job_search_plugin"
