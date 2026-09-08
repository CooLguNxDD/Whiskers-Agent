"""Unit tests for the dynamic LLM provider registry (core/llm_provider_management)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.llm_provider_management import (
    LLMProvider,
    ProviderSpec,
    get_chat_llm,
    get_llm_provider_registry,
    make_embeddings,
    make_llm,
    register_provider,
)

_BUILTIN_IDS = {
    "openai",
    "anthropic",
    "gemini",
    "gemini-vertex",
    "voyage",
    "claude-cli",
    "agy-cli",
    "grok-cli",
}


def test_registry_seeds_all_builtin_providers():
    reg = get_llm_provider_registry()
    assert _BUILTIN_IDS.issubset(reg.ids())


def test_registry_get_returns_spec_with_expected_shape():
    reg = get_llm_provider_registry()
    spec = reg.get("openai")
    assert isinstance(spec, ProviderSpec)
    assert spec.id == "openai"
    assert spec.is_cli is False
    assert spec.env_key == "OPENAI_API_KEY"
    assert callable(spec.chat_factory)
    assert callable(spec.embeddings_factory)
    assert callable(spec.is_available)


def test_registry_get_unknown_provider_returns_none():
    reg = get_llm_provider_registry()
    assert reg.get("does-not-exist") is None


def test_cli_providers_without_voyage_have_no_embeddings():
    """agy/grok are chat-only; claude-cli uses Voyage (not OpenAI fallback)."""
    reg = get_llm_provider_registry()
    for pid in ("agy-cli", "grok-cli"):
        spec = reg.get(pid)
        assert spec is not None
        assert spec.is_cli is True
        assert spec.env_key is None
        assert spec.embeddings_factory is None

    claude = reg.get("claude-cli")
    assert claude is not None
    assert claude.is_cli is True
    assert callable(claude.embeddings_factory)
    assert claude.default_embed_model == "voyage-4"


def test_make_llm_unknown_provider_raises_value_error():
    with pytest.raises(ValueError):
        make_llm("totally-unknown-provider", "some-model")


def test_make_embeddings_unknown_provider_raises_value_error():
    with pytest.raises(ValueError):
        make_embeddings("totally-unknown-provider", "some-model", 1536)


def test_make_embeddings_agy_cli_does_not_fall_back_to_openai():
    """Chat-only CLI providers must not silently use OpenAI embeddings."""
    with pytest.raises(ValueError, match="no embeddings API"):
        make_embeddings("agy-cli", "text-embedding-3-small", 1536, api_key="sk-test")


def test_make_embeddings_claude_cli_uses_voyage(monkeypatch):
    """claude-cli uses Voyage AI embeddings — never OpenAI."""
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-test-key")
    with patch(
        "core.llm_provider_management.providers.voyage.make_voyage_embeddings"
    ) as mock_fn:
        mock_fn.return_value = object()
        emb = make_embeddings("claude-cli", "voyage-4", 1024, api_key="voyage-test-key")
        mock_fn.assert_called_once()
        assert emb is mock_fn.return_value


def test_make_embeddings_anthropic_uses_voyage(monkeypatch):
    """anthropic uses Voyage AI embeddings — never OpenAI."""
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-test-key")
    with patch(
        "core.llm_provider_management.providers.voyage.make_voyage_embeddings"
    ) as mock_fn:
        mock_fn.return_value = object()
        emb = make_embeddings("anthropic", "voyage-4", 1024, api_key="voyage-test-key")
        mock_fn.assert_called_once()
        assert emb is mock_fn.return_value


def test_make_embeddings_voyage_provider(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-test-key")
    with patch(
        "core.llm_provider_management.providers.voyage.make_voyage_embeddings"
    ) as mock_fn:
        mock_fn.return_value = object()
        emb = make_embeddings("voyage", "voyage-4", 1024)
        mock_fn.assert_called_once()
        assert emb is mock_fn.return_value


def test_register_provider_adds_out_of_tree_spec():
    """A caller can register a brand-new provider without touching an enum
    or any hardcoded factory dict — the decoupling this refactor is for."""
    reg = get_llm_provider_registry()
    fake_chat = MagicMock(return_value="fake-chat-client")
    spec = ProviderSpec(
        id="test-out-of-tree-provider",
        chat_factory=lambda model, api_key, base_url: fake_chat(model, api_key, base_url),
        is_available=lambda: True,
    )
    try:
        register_provider(spec)
        assert reg.get("test-out-of-tree-provider") is spec
        result = make_llm("test-out-of-tree-provider", "some-model")
        assert result == "fake-chat-client"
        fake_chat.assert_called_once_with("some-model", None, None)
    finally:
        reg.unregister("test-out-of-tree-provider")


def test_get_chat_llm_caches_by_provider_model_key():
    from core.llm_provider_management.factory import _llm_cache

    key_provider = "test-cache-provider"
    built: list[object] = []

    def _factory(model, api_key, base_url):
        obj = object()
        built.append(obj)
        return obj

    spec = ProviderSpec(id=key_provider, chat_factory=_factory, is_available=lambda: True)
    reg = get_llm_provider_registry()
    try:
        register_provider(spec)
        first = get_chat_llm(key_provider, "cache-test-model")
        second = get_chat_llm(key_provider, "cache-test-model")
        assert first is second
        assert len(built) == 1
    finally:
        reg.unregister(key_provider)
        _llm_cache.pop((key_provider, "cache-test-model", "", ""), None)
