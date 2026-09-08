"""Unit tests for CLI parent-env secret denylist."""

from __future__ import annotations

from core_graph.goap_agent.cli.env_filter import is_secret_env_key, sanitized_parent_env


def test_is_secret_env_key_exact_and_suffix():
    assert is_secret_env_key("MASTER_KEY") is True
    assert is_secret_env_key("DATABASE_URL") is True
    assert is_secret_env_key("OPENAI_API_KEY") is True
    assert is_secret_env_key("FOO_API_KEY") is True
    assert is_secret_env_key("SERVICE_SECRET") is True
    assert is_secret_env_key("DB_PASSWORD") is True
    assert is_secret_env_key("SOME_TOKEN") is True
    assert is_secret_env_key("PATH") is False
    assert is_secret_env_key("HOME") is False
    assert is_secret_env_key("WHISKERS_API_URL") is False  # URL, not secret suffix


def test_allowlist_kept():
    for key in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "GOAP_AGENT_MCP_TOKEN",
        "CLI_AGENT_MCP_TOKEN",
    ):
        assert is_secret_env_key(key) is False


def test_sanitized_parent_env_drops_secrets_keeps_allowlist():
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        "MASTER_KEY": "super-secret",
        "DATABASE_URL": "postgresql://x",
        "OPENAI_API_KEY": "sk-openai",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth-tok",
        "ANTHROPIC_API_KEY": "sk-ant",
        "GOAP_AGENT_MCP_TOKEN": "mcp-tok",
        "CUSTOM_API_KEY": "drop-me",
    }
    out = sanitized_parent_env(source)
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/home/user"
    assert out["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-tok"
    assert out["ANTHROPIC_API_KEY"] == "sk-ant"
    assert out["GOAP_AGENT_MCP_TOKEN"] == "mcp-tok"
    assert "MASTER_KEY" not in out
    assert "DATABASE_URL" not in out
    assert "OPENAI_API_KEY" not in out
    assert "CUSTOM_API_KEY" not in out


def test_options_env_override_wins_after_sanitize(monkeypatch):
    """Runner applies denylist first; options.env can re-introduce allowlisted keys."""
    monkeypatch.setenv("MASTER_KEY", "parent-master")
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    env = sanitized_parent_env()
    assert "MASTER_KEY" not in env
    # Simulate options.env merge (same as runner.py)
    options_env = {"ANTHROPIC_API_KEY": "pool-key", "MASTER_KEY": "should-still-be-settable"}
    for ek, ev in options_env.items():
        env[ek] = ev
    assert env["ANTHROPIC_API_KEY"] == "pool-key"
    # Explicit override may set any key (denylist only applies to parent copy)
    assert env["MASTER_KEY"] == "should-still-be-settable"
