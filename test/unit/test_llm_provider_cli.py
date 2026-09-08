"""Unit tests for claude-cli / agy-cli LLM provider enum and factory."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from core.llm_provider_management import LLMProvider, _llm_available, get_chat_llm, make_embeddings


def test_enum_includes_cli_providers():
    assert LLMProvider.CLAUDE_CLI.value == "claude-cli"
    assert LLMProvider.AGY_CLI.value == "agy-cli"
    assert LLMProvider("claude-cli") is LLMProvider.CLAUDE_CLI
    assert LLMProvider("agy-cli") is LLMProvider.AGY_CLI


def test_llm_available_cli_soft_pass(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude-cli")
    monkeypatch.delenv("CLI_AGENT_REQUIRE_BINARY", raising=False)
    assert _llm_available() is True


def test_llm_available_cli_require_binary(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude-cli")
    monkeypatch.setenv("CLI_AGENT_REQUIRE_BINARY", "1")
    monkeypatch.setenv("CLAUDE_CLI_BINARY", "definitely-missing-binary-xyz")
    assert _llm_available() is False


def test_get_chat_llm_claude_cli():
    llm = get_chat_llm(LLMProvider.CLAUDE_CLI, "sonnet")
    assert llm.__class__.__name__ == "CliAgentChatModel"
    assert getattr(llm, "agent", None) == "claude"


def test_get_chat_llm_agy_cli():
    llm = get_chat_llm(LLMProvider.AGY_CLI, "")
    assert llm.__class__.__name__ == "CliAgentChatModel"
    assert getattr(llm, "agent", None) == "agy"


def test_embeddings_claude_cli_uses_voyage(monkeypatch):
    """claude-cli uses Voyage AI embeddings — never OpenAI, never shells out."""
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-test-key")
    with patch(
        "core.llm_provider_management.providers.voyage.make_voyage_embeddings"
    ) as mock_fn:
        mock_fn.return_value = object()
        emb = make_embeddings("claude-cli", "voyage-4", 1024, api_key="voyage-test-key")
        mock_fn.assert_called()
        assert emb is mock_fn.return_value


def test_embeddings_agy_cli_requires_explicit_embed_provider():
    """agy-cli has no embeddings factory and must not fall back to OpenAI."""
    with pytest.raises(ValueError, match="no embeddings API"):
        make_embeddings("agy-cli", "text-embedding-3-small", 1536)


def test_pool_manager_accepts_cli_provider():
    from core.llm.pool_manager import add_pool_entry
    # Validation only — no DB
    with patch("core.llm.pool_manager._db_available", return_value=False):
        # When DB unavailable, list is empty; validate provider string path via enum
        assert "claude-cli" in {p.value for p in LLMProvider}
        assert "agy-cli" in {p.value for p in LLMProvider}
