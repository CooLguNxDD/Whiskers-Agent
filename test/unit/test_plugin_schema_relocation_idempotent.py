"""Relocation smoke: plugin migration modules expose upgrade(conn) and are loadable."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from db_layer.plugin_schema_migrator import PluginSchemaMigrator

# First-party packs relocated off Alembic plugin branches.
_PLUGIN_MIG_DIRS = [
    Path("plugins/search_plugin/migrations"),
    Path("plugins/job_search_plugin/migrations"),
    Path("plugins/portfolio_plugin/migrations"),
]


@pytest.mark.parametrize("mig_dir", _PLUGIN_MIG_DIRS, ids=lambda p: p.parts[1])
def test_relocated_steps_discoverable(mig_dir: Path):
    assert mig_dir.is_dir(), f"missing migrations dir: {mig_dir}"
    steps = PluginSchemaMigrator().discover_steps(mig_dir)
    assert steps, f"no steps under {mig_dir}"
    # Lexicographic + numbered prefix
    revs = [s.revision for s in steps]
    assert revs == sorted(revs)
    for s in steps:
        assert s.checksum


@pytest.mark.parametrize("mig_dir", _PLUGIN_MIG_DIRS, ids=lambda p: p.parts[1])
def test_py_steps_export_upgrade(mig_dir: Path):
    steps = PluginSchemaMigrator().discover_steps(mig_dir)
    for s in steps:
        if s.kind != "py":
            continue
        spec = importlib.util.spec_from_file_location(s.revision, s.path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert callable(getattr(mod, "upgrade", None)), s.path
