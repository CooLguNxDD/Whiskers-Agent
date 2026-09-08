"""Unit tests for plugin on-disk config path safety."""

from __future__ import annotations

from core.plugin_loader.config_file_store import (
    _safe_plugin_id,
    load_base_config,
    resolve_config_filename,
)


def test_safe_plugin_id_rejects_traversal() -> None:
    assert _safe_plugin_id("fake_plugin") is True
    assert _safe_plugin_id("../etc") is False
    assert _safe_plugin_id("foo/bar") is False
    assert _safe_plugin_id("") is False
    assert _safe_plugin_id("..") is False


def test_resolve_config_filename_safe_on_bad_id() -> None:
    assert resolve_config_filename("../etc/passwd") == "config.json"
    assert resolve_config_filename("") == "config.json"


def test_load_base_config_rejects_traversal() -> None:
    data, filename = load_base_config("../etc/passwd")
    assert data is None
    assert filename == "config.json"

    data2, filename2 = load_base_config("foo/../../secrets")
    assert data2 is None
    assert filename2 == "config.json"
