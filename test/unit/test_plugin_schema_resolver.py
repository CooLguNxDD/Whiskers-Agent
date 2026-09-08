"""Resolver parse_schema_cfg + build_specs schema_cfg wiring."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from core.plugin_loader.resolver import parse_schema_cfg, build_specs


def test_parse_schema_cfg_defaults(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo_plugin"
    mig = plugin_dir / "migrations"
    mig.mkdir(parents=True)

    with patch("core.plugin_loader.resolver.PROJECT_ROOT", tmp_path):
        cfg = parse_schema_cfg({}, plugin_dir)

    assert cfg is not None
    assert cfg["migrations_path"] == "migrations"
    assert cfg["auto_migrate"] is True
    assert cfg["min_core_revision"] is None
    assert Path(cfg["migrations_dir"]) == mig.resolve()


def test_parse_schema_cfg_from_manifest(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "migrations").mkdir()

    manifest = {
        "schema": {
            "migrations_path": "migrations",
            "min_core_revision": "core_039",
            "auto_migrate": True,
        }
    }
    with patch("core.plugin_loader.resolver.PROJECT_ROOT", tmp_path):
        cfg = parse_schema_cfg(manifest, plugin_dir)

    assert cfg["min_core_revision"] == "core_039"
    assert cfg["auto_migrate"] is True


def test_parse_schema_cfg_rejects_path_traversal(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo_plugin"
    plugin_dir.mkdir(parents=True)

    manifest = {"schema": {"migrations_path": "../../etc"}}
    with patch("core.plugin_loader.resolver.PROJECT_ROOT", tmp_path):
        cfg = parse_schema_cfg(manifest, plugin_dir)

    assert cfg is None


def test_parse_schema_cfg_absent_block_and_no_dir_is_none(tmp_path):
    plugin_dir = tmp_path / "plugins" / "demo_plugin"
    plugin_dir.mkdir(parents=True)

    with patch("core.plugin_loader.resolver.PROJECT_ROOT", tmp_path):
        cfg = parse_schema_cfg({}, plugin_dir)

    assert cfg is None


def test_build_specs_attaches_schema_cfg(tmp_path):
    plugins = tmp_path / "plugins"
    pkg_dir = plugins / "demo_plugin"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "migrations").mkdir()
    (pkg_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "demo_plugin",
                "version": "1.0.0",
                "tier": "free",
                "schema": {
                    "migrations_path": "migrations",
                    "min_core_revision": "core_040",
                    "auto_migrate": True,
                },
            }
        ),
        encoding="utf-8",
    )

    with patch("core.plugin_loader.resolver.PROJECT_ROOT", tmp_path), patch(
        "core.plugin_loader.content_hash.compute_plugin_tree_hash",
        return_value="sha256:x",
    ):
        specs, skipped = build_specs(["plugins.demo_plugin"])

    assert not skipped
    assert len(specs) == 1
    assert specs[0].schema_cfg is not None
    assert specs[0].schema_cfg["min_core_revision"] == "core_040"
