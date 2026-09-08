import pytest
from utils import tools_api_config

def test_get_endpoint_meta_fallback_to_base(monkeypatch):
    monkeypatch.setattr(tools_api_config, "_ENDPOINT_META_BASE", {"timeout": 10, "retries": 3})
    monkeypatch.setattr(tools_api_config, "ENDPOINT_META", {})

    result = tools_api_config.get_endpoint_meta("unknown_tool")
    assert result == {"timeout": 10, "retries": 3}

def test_get_endpoint_meta_override_base(monkeypatch):
    monkeypatch.setattr(tools_api_config, "_ENDPOINT_META_BASE", {"timeout": 10, "retries": 3})
    monkeypatch.setattr(tools_api_config, "ENDPOINT_META", {"known_tool": {"timeout": 20, "extra": "value"}})

    result = tools_api_config.get_endpoint_meta("known_tool")
    assert result == {"timeout": 20, "retries": 3, "extra": "value"}

def test_get_endpoint_meta_returns_fresh_dict(monkeypatch):
    base = {"timeout": 10}
    monkeypatch.setattr(tools_api_config, "_ENDPOINT_META_BASE", base)
    monkeypatch.setattr(tools_api_config, "ENDPOINT_META", {})

    result = tools_api_config.get_endpoint_meta("any_tool")
    result["timeout"] = 99

    # Base should not be mutated
    assert base["timeout"] == 10
