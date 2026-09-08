import pytest
from pathlib import Path
from core.config_loader import _plugin_key

def test_plugin_key_with_name():
    manifest = {"name": "test-plugin"}
    key = _plugin_key("", manifest)
    assert key == "test-plugin"

def test_plugin_key_with_path_no_name():
    manifest = {}
    path = "core/config_loader.py"
    key = _plugin_key(path, manifest)
    assert key == str(Path(path).resolve())

def test_plugin_key_falsy_path_no_name():
    manifest = {}
    with pytest.raises(ValueError) as excinfo:
        _plugin_key("", manifest)
    assert "manifest_path is required" in str(excinfo.value)

def test_plugin_key_none_path_no_name():
    with pytest.raises(ValueError) as excinfo:
        _plugin_key(None, None)
    assert "manifest_path is required" in str(excinfo.value)
