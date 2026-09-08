"""Design surface lives in design_systems/<id>/settings.json, not manifest."""

from __future__ import annotations

import json
from pathlib import Path

from plugins.portfolio_plugin.plugin_config import (
    DESIGN_SETTING_KEYS,
    SETTINGS,
    _load_design_settings,
    reload_settings,
)


# plugins/portfolio_plugin/tests/this_file.py → parents[1] = plugin package dir
_PLUGIN = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[3]


def test_manifest_has_no_design_surface_keys():
    manifest = json.loads((_PLUGIN / "manifest.json").read_text(encoding="utf-8"))
    settings = manifest.get("settings") or {}
    for key in DESIGN_SETTING_KEYS:
        assert key not in settings, f"design key {key!r} must not live in manifest.json"


def test_manifest_keeps_operational_keys():
    manifest = json.loads((_PLUGIN / "manifest.json").read_text(encoding="utf-8"))
    settings = manifest.get("settings") or {}
    assert "discovery" in settings
    assert "github_allowlist" in settings
    assert "design_system" in settings
    assert isinstance(settings.get("portfolio_layout"), dict)
    assert settings["portfolio_layout"].get("mode") in ("auto", "agentic", "fast")


def test_portfolio_layout_not_in_server_config_example():
    example = json.loads(
        (_REPO / "config" / "server_config_example.json").read_text(
            encoding="utf-8"
        )
    )
    graph = example.get("graph") or {}
    assert "portfolio_layout" not in graph


def test_design_settings_file_has_expected_keys():
    data = _load_design_settings("default")
    assert "hero" in data
    assert data["hero"].get("name")
    assert "layout_presets" in data
    assert "showcase" in data["layout_presets"]
    assert "quick_actions" in data
    assert "design_tokens" in data


def test_settings_merge_exposes_design_and_ops():
    reload_settings()
    assert SETTINGS.get("hero", {}).get("name")
    assert "showcase" in (SETTINGS.get("layout_presets") or {})
    assert SETTINGS.get("discovery", {}).get("enabled") is True
    assert SETTINGS.get("github_allowlist", {}).get("owners")
    # operational design_system id
    assert SETTINGS.get("design_system") == "default"


def test_load_design_system_includes_settings_package():
    from plugins.portfolio_plugin.render.design_system import clear_design_system_cache, load_design_system

    clear_design_system_cache()
    ds = load_design_system("default")
    assert ds.get("settings", {}).get("hero")
    assert ds.get("design_tokens") or ds["settings"].get("design_tokens")
