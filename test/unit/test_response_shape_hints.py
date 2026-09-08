"""Unit tests for the core-owned response-shaping hint text (utils.response_shape_hints).

Guards against the drift that motivated this module: old hand-written prose in
plugin config.json files silently fell out of sync with the actual shaping
pipeline vocabulary. These tests assert the rendered hints stay derived from
the real pipeline keys/formats, not just static strings someone can forget to update.
"""

from __future__ import annotations

import pytest

from utils.response_shape import _PIPELINE_MAP
from utils.response_format import ResponseFormat
from utils.response_shape_hints import (
    DEFAULT_FORMAT_HINT,
    DEFAULT_SHAPE_HINT,
    SHAPE_PARAM_DESCRIPTION,
    get_format_hint,
    get_response_hints,
    get_shape_hint,
)


def test_shape_hint_covers_every_pipeline_key():
    """Every key the shaping pipeline actually reads must appear in the rendered hint."""
    for key in _PIPELINE_MAP:
        if key == "offload_minio":
            continue  # documented under its own name below; assert separately
        assert f"`{key}`" in DEFAULT_SHAPE_HINT, f"pipeline key {key!r} undocumented in shape hint"
    assert "offload_minio" in DEFAULT_SHAPE_HINT


def test_shape_hint_covers_envelope_keys():
    """meta_only/include_meta/passthrough aren't in _PIPELINE_MAP but are real shape dict keys."""
    for key in ("include_meta", "meta_only", "passthrough"):
        assert f"`{key}`" in DEFAULT_SHAPE_HINT


def test_format_hint_covers_every_response_format_member():
    for member in ResponseFormat:
        assert f'"{member.value}"' in DEFAULT_FORMAT_HINT, f"ResponseFormat.{member.name} undocumented"


def test_format_hint_covers_format_scoped_keys():
    for key in ("group_by", "id_key"):
        assert key in DEFAULT_FORMAT_HINT


def test_shape_param_description_is_nonempty_and_short():
    assert SHAPE_PARAM_DESCRIPTION
    assert len(SHAPE_PARAM_DESCRIPTION) < 400  # stays a one-paragraph schema description


def test_get_response_hints_matches_individual_getters():
    assert get_response_hints() == (get_shape_hint(), get_format_hint())


def test_config_override_replaces_default(monkeypatch):
    class _Cfg:
        tools_api = {"hints": {"shape_hint": ["custom shape text"], "format_hint": "custom format text"}}

    monkeypatch.setattr("utils.response_shape_hints.get_config_registry", lambda: _Cfg())
    assert get_shape_hint() == "custom shape text"
    assert get_format_hint() == "custom format text"


def test_no_override_falls_back_to_default(monkeypatch):
    class _Cfg:
        tools_api: dict = {}

    monkeypatch.setattr("utils.response_shape_hints.get_config_registry", lambda: _Cfg())
    assert get_shape_hint() == DEFAULT_SHAPE_HINT
    assert get_format_hint() == DEFAULT_FORMAT_HINT


def test_override_lookup_fails_soft(monkeypatch):
    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr("utils.response_shape_hints.get_config_registry", _raise)
    assert get_shape_hint() == DEFAULT_SHAPE_HINT
    assert get_format_hint() == DEFAULT_FORMAT_HINT


class TestConfigLoaderMerge:
    """core/config_loader.get_response_hints must prepend the core text, not replace it,
    and must no longer push shape/format hints into the server-wide MCP instructions."""

    def test_example_config_does_not_override_core_hints(self):
        """setup.py env copies this file; a placeholder hints block would replace the pipeline docs."""
        import json
        from pathlib import Path

        data = json.loads(
            (Path(__file__).resolve().parents[2] / "config" / "tools_api_config_example.json").read_text(
                encoding="utf-8"
            )
        )
        hints = data.get("hints") or {}
        assert not hints.get("shape_hint"), "example must omit shape_hint so scaffold keeps core text"
        assert not hints.get("format_hint"), "example must omit format_hint so scaffold keeps core text"


    def test_core_hint_present_with_no_plugin_config(self, monkeypatch):
        from core import config_loader

        class _Cfg:
            tools_api: dict = {}

        monkeypatch.setattr("utils.response_shape_hints.get_config_registry", lambda: _Cfg())
        shape, fmt = config_loader.get_response_hints("__no_such_plugin__")
        assert shape == DEFAULT_SHAPE_HINT
        assert fmt == DEFAULT_FORMAT_HINT

    def test_plugin_hint_appended_after_core_text(self, monkeypatch):
        from core import config_loader

        class _Cfg:
            tools_api: dict = {}

        monkeypatch.setattr("utils.response_shape_hints.get_config_registry", lambda: _Cfg())
        config_loader.SHAPE_HINTS["__test_plugin_key__"] = "plugin-specific shape note"
        config_loader.FORMAT_HINTS["__test_plugin_key__"] = "plugin-specific format note"
        try:
            shape, fmt = config_loader.get_response_hints("__test_plugin_key__")
        finally:
            config_loader.SHAPE_HINTS.pop("__test_plugin_key__", None)
            config_loader.FORMAT_HINTS.pop("__test_plugin_key__", None)

        assert shape.startswith(DEFAULT_SHAPE_HINT)
        assert "plugin-specific shape note" in shape
        assert fmt.startswith(DEFAULT_FORMAT_HINT)
        assert "plugin-specific format note" in fmt

    def test_load_plugin_config_no_longer_mutates_mcp_instructions(self, tmp_path):
        from core import config_loader

        config_file = tmp_path / "config.json"
        config_file.write_text('{"shape_hint": ["x"], "format_hint": ["y"]}', encoding="utf-8")
        manifest_path = str(tmp_path / "manifest.json")
        manifest = {"name": "__mutation_test_plugin__", "config": "config.json"}

        class _FakeBuilder:
            def __init__(self):
                self.calls = []

            def add_context(self, s):
                self.calls.append(s)

            def update_mcp_instructions(self, mcp_app):
                raise AssertionError("update_mcp_instructions must not be called from load_plugin_config")

        builder = _FakeBuilder()
        config_loader.load_plugin_config(manifest_path, manifest, mcp_context_builder=builder, mcp=object())
        assert builder.calls == []
