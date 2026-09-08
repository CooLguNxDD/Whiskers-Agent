"""Unit tests for per-agent MCP discovery file placement."""

from __future__ import annotations

import json
from pathlib import Path

from core_graph.goap_agent.cli.mcp_place import (
    claude_config_to_agy,
    claude_config_to_grok_toml_section,
    place_mcp_for_agent,
)
from core_graph.goap_agent.cli_inject import build_mcp_config_dict, write_mcp_config_file


def test_claude_config_to_agy_uses_server_url():
    cfg = build_mcp_config_dict(url="http://127.0.0.1:10000/mcp", token="t")
    out = claude_config_to_agy(cfg)
    entry = out["mcpServers"].get("whiskers-goap") or out["mcpServers"].get("whiskers-goap")
    assert entry is not None
    assert entry["serverUrl"] == "http://127.0.0.1:10000/mcp"
    assert "url" not in entry
    assert "type" not in entry
    assert entry["headers"]["Authorization"] == "Bearer t"


def test_claude_config_to_grok_toml():
    cfg = build_mcp_config_dict(url="http://127.0.0.1:10000/mcp", token='tok"x')
    section = claude_config_to_grok_toml_section(cfg)
    assert "[mcp_servers.whiskers-goap]" in section or "[mcp_servers.whiskers-goap]" in section
    assert "url =" in section
    assert "enabled = true" in section
    assert "headers" in section
    assert "whiskers-goap-agent mcp" in section or "whiskers-goap-agent mcp" in section


def test_place_claude_is_noop(tmp_path):
    cfg = build_mcp_config_dict(url="http://x/mcp", token="t")
    path = write_mcp_config_file(cfg, directory=str(tmp_path))
    placement = place_mcp_for_agent("claude", workdir=str(tmp_path), mcp_config_path=path)
    assert placement.meta["strategy"] == "cli_flag"
    assert not list(tmp_path.glob("**/.mcp.json"))
    placement.cleanup()


def test_place_grok_writes_and_restores(tmp_path):
    cfg = build_mcp_config_dict(url="http://127.0.0.1:10000/mcp", token="secret")
    path = write_mcp_config_file(cfg, directory=str(tmp_path / "inj"))
    grok_cfg = tmp_path / ".grok" / "config.toml"
    grok_cfg.parent.mkdir(parents=True)
    grok_cfg.write_text("# existing\n[other]\nx = 1\n", encoding="utf-8")

    placement = place_mcp_for_agent("grok", workdir=str(tmp_path), mcp_config_path=path)
    body = grok_cfg.read_text(encoding="utf-8")
    assert "# existing" in body
    assert "whiskers-goap" in body or "whiskers-goap" in body
    assert "Bearer secret" in body

    placement.cleanup()
    restored = grok_cfg.read_text(encoding="utf-8")
    assert restored == "# existing\n[other]\nx = 1\n"


def test_place_agy_writes_and_deletes(tmp_path):
    cfg = build_mcp_config_dict(url="http://127.0.0.1:10000/mcp", token="t")
    path = write_mcp_config_file(cfg, directory=str(tmp_path / "inj"))
    placement = place_mcp_for_agent("agy", workdir=str(tmp_path), mcp_config_path=path)
    target = tmp_path / ".agents" / "mcp_config.json"
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    entry = data["mcpServers"].get("whiskers-goap") or data["mcpServers"].get("whiskers-goap")
    assert entry is not None
    assert entry["serverUrl"].endswith("/mcp")
    placement.cleanup()
    assert not target.exists()
