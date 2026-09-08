"""Unit tests for PluginLifecycleManager._load_flow_specs path-traversal guard."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.plugin_loader.lifecycle_manager import PluginLifecycleManager
from core_graph.subgraphs.specialist.flow_registry import clear_flows, get_flow_registry


@pytest.fixture(autouse=True)
def _clean():
    clear_flows()
    yield
    clear_flows()


def _spec(tmp_path: Path, name: str = "fake_plugin") -> SimpleNamespace:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    return SimpleNamespace(name=name, manifest_path=manifest_path)


def test_valid_relative_path_registers_flow(tmp_path):
    flow_dir = tmp_path / "flow_specs"
    flow_dir.mkdir()
    flow_file = flow_dir / "f1.json"
    flow_file.write_text(
        json.dumps(
            {
                "flow_id": "f1",
                "name": "F1",
                "stages": [{"id": "s1", "kind": "deterministic", "op": "p/o"}],
            }
        ),
        encoding="utf-8",
    )
    spec = _spec(tmp_path)
    PluginLifecycleManager._load_flow_specs(spec, ["flow_specs/f1.json"])
    assert get_flow_registry().get("f1") is not None
    assert get_flow_registry().get("f1").owner == "fake_plugin"


def test_parent_traversal_rejected(tmp_path):
    outside = tmp_path.parent / "escaped.json"
    outside.write_text(
        json.dumps({"flow_id": "evil", "name": "E", "stages": [{"id": "s", "kind": "deterministic", "op": "p/o"}]}),
        encoding="utf-8",
    )
    spec = _spec(tmp_path)
    PluginLifecycleManager._load_flow_specs(spec, ["../escaped.json"])
    assert get_flow_registry().get("evil") is None


def test_absolute_path_rejected(tmp_path):
    outside = tmp_path.parent / "abs.json"
    outside.write_text(
        json.dumps({"flow_id": "evil2", "name": "E2", "stages": [{"id": "s", "kind": "deterministic", "op": "p/o"}]}),
        encoding="utf-8",
    )
    spec = _spec(tmp_path)
    PluginLifecycleManager._load_flow_specs(spec, [str(outside)])
    assert get_flow_registry().get("evil2") is None


def test_missing_file_does_not_raise(tmp_path):
    spec = _spec(tmp_path)
    # Must not raise — plugin load must not fail on a bad flow_specs entry.
    PluginLifecycleManager._load_flow_specs(spec, ["flow_specs/does_not_exist.json"])
    assert get_flow_registry().list_flows() == []


def test_invalid_json_does_not_raise(tmp_path):
    flow_dir = tmp_path / "flow_specs"
    flow_dir.mkdir()
    (flow_dir / "bad.json").write_text("{not json", encoding="utf-8")
    spec = _spec(tmp_path)
    PluginLifecycleManager._load_flow_specs(spec, ["flow_specs/bad.json"])
    assert get_flow_registry().list_flows() == []
