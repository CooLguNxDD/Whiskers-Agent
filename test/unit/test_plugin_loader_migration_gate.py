"""Loader fail-closed migration gate in _load_one_spec."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.plugin_loader.lifecycle_manager import PluginLifecycleManager

_load_one_spec = PluginLifecycleManager.load_one_spec
from core.plugin_loader.resolver import PluginSpec
from db_layer.plugin_schema_migrator import PluginMigrationError
from db_layer.plugin_registry_store import PluginMetaKey


def _spec(
    name: str = "demo_plugin",
    *,
    schema_cfg: dict | None = None,
    content_hash: str = "sha256:abc",
) -> PluginSpec:
    return PluginSpec(
        package=f"plugins.{name}",
        name=name,
        tier=1,
        requires=(),
        required_credentials=(),
        manifest_path=Path(f"plugins/{name}/manifest.json"),
        manifest={"name": name, "version": "1.0.0"},
        content_hash=content_hash,
        schema_cfg=schema_cfg
        or {
            "migrations_path": "migrations",
            "migrations_dir": f"plugins/{name}/migrations",
            "min_core_revision": None,
            "auto_migrate": True,
        },
    )


@pytest.mark.asyncio
async def test_load_one_spec_migration_failure_sets_meta_and_returns_false():
    registry = MagicMock()
    registry.system_tier = 999
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(meta={}))
    db.update_plugin_meta = AsyncMock()
    db.set_content_hash = AsyncMock()
    vault = MagicMock()
    fake_pkg = SimpleNamespace(register=MagicMock())

    with patch(
        "core.plugin_loader.lifecycle_manager.PluginLifecycleManager.db_upsert_and_validate",
        new_callable=AsyncMock,
    ), patch(
        "core.plugin_loader.lifecycle_manager.PluginLifecycleManager.run_plugin_migrations",
        new_callable=AsyncMock,
        side_effect=PluginMigrationError("step boom"),
    ), patch(
        "core.plugin_loader.lifecycle_manager.PluginLifecycleManager.persist_spec_content_hash",
        new_callable=AsyncMock,
    ) as persist, patch(
        "core.plugin_loader.lifecycle_manager.load_plugin_config",
    ), patch(
        "importlib.import_module",
        return_value=fake_pkg,
    ) as import_mod:
        ok = await _load_one_spec(
            registry,
            _spec(),
            db_registry=db,
            vault=vault,
            shortname_to_manifest_name={},
            manifests_by_shortname={},
        )

    assert ok is False
    db.update_plugin_meta.assert_awaited()
    meta_arg = db.update_plugin_meta.await_args.args[1]
    assert PluginMetaKey.MIGRATION_ERROR.value in meta_arg
    assert "boom" in meta_arg[PluginMetaKey.MIGRATION_ERROR.value]
    # Tools never registered; package never imported on migration failure
    fake_pkg.register.assert_not_called()
    import_mod.assert_not_called()
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_one_spec_migration_success_clears_meta():
    registry = MagicMock()
    registry.system_tier = 999
    existing_meta = {PluginMetaKey.MIGRATION_ERROR.value: "old error"}
    db = AsyncMock()
    db.get = AsyncMock(
        return_value=SimpleNamespace(meta=existing_meta, content_hash=None)
    )
    db.update_plugin_meta = AsyncMock()
    db.set_content_hash = AsyncMock()
    db.get_skills = AsyncMock(return_value={})
    db.get_scopes = AsyncMock(return_value=([], ""))
    db.set_scopes = AsyncMock()
    vault = MagicMock()
    fake_pkg = SimpleNamespace(register=MagicMock())

    with patch(
        "core.plugin_loader.lifecycle_manager.PluginLifecycleManager.db_upsert_and_validate",
        new_callable=AsyncMock,
    ), patch(
        "core.plugin_loader.lifecycle_manager.PluginLifecycleManager.run_plugin_migrations",
        new_callable=AsyncMock,
    ) as run_mig, patch(
        "core.plugin_loader.lifecycle_manager.PluginLifecycleManager.persist_spec_content_hash",
        new_callable=AsyncMock,
    ), patch(
        "core.plugin_loader.lifecycle_manager.load_plugin_config",
    ), patch(
        "core.plugin_loader.lifecycle_manager.set_plugin_skills",
    ), patch(
        "core.plugin_loader.lifecycle_manager.set_plugin_scopes",
    ), patch(
        "importlib.import_module",
        return_value=fake_pkg,
    ) as import_mod:
        ok = await _load_one_spec(
            registry,
            _spec(),
            db_registry=db,
            vault=vault,
            shortname_to_manifest_name={},
            manifests_by_shortname={},
        )

    # Migration success clears meta before register; overall load may still
    # hit non-fatal scope-persist paths depending on env — assert gate only.
    run_mig.assert_awaited_once()
    assert db.update_plugin_meta.await_count >= 1
    cleared = any(
        PluginMetaKey.MIGRATION_ERROR.value not in call.args[1]
        for call in db.update_plugin_meta.await_args_list
    )
    assert cleared, "expected migration_error to be cleared on success"
    # import + register only after successful migration
    import_mod.assert_called()
    fake_pkg.register.assert_called_once()
