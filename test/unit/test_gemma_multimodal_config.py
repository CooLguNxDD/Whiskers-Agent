"""Additional fail-closed configuration and shared-resolution regression coverage."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from db_layer.embeddings import embeddings_core as core
from db_layer.embeddings.multimodal import EmbeddingInput


@pytest.fixture
def selection():
    return {"provider": "gemma-multimodal", "model": "mm-alias", "dimensions": 3,
            "base_url": "https://gateway.example.invalid/v1"}


@pytest.mark.parametrize("change", [
    {"model": None}, {"dimensions": None}, {"dimensions": 0}, {"dimensions": -1},
    {"dimensions": True}, {"dimensions": 3.5}, {"base_url": None},
    {"base_url": "file:///local"}, {"base_url": "https://gateway.example.invalid/v1?token=invalid"},
    {"batch_size": 0}, {"max_concurrency": 0}, {"max_concurrency": 1000},
])
def test_invalid_config_fails_before_creating_transport(selection, change, monkeypatch):
    for key in ("EMBED_API_KEY", "EMBED_BASE_URL", "VOYAGE_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    transport = MagicMock(side_effect=AssertionError("must remain lazy"))
    monkeypatch.setattr("core.proxy.ssrf_safety._safe_async_client", transport)
    with pytest.raises(ValueError):
        core._make_embeddings_from({**selection, **change})
    transport.assert_not_called()


@pytest.mark.asyncio
async def test_pool_resolution_and_profile_batch_options(selection, monkeypatch):
    import core.llm_config_service as config
    from core.embedding_dimensions import embedding_dimensions
    monkeypatch.setattr(config, "_db_available", lambda: True)
    monkeypatch.setattr(config, "get_active", AsyncMock(return_value=selection))
    monkeypatch.setattr("utils.embedding_config.EMBEDDING_CONFIG", {
        "models": {"mm": {**selection, "batch_size": 1, "max_concurrency": 1}}})
    monkeypatch.setattr(core, "_model_clients", {})
    resolved = await config.resolve_embedding()
    assert resolved["source"] == "pool"
    assert resolved["dimensions"] == 3
    client = await core._get_client_for(resolved)
    assert client.batch_size == client.max_concurrency == 1
    assert (await core._get_client_for({**resolved, "batch_size": 2})).batch_size == 2

    client.aembed_multimodal = AsyncMock(return_value=[[1, 2, 3]])
    docs = [EmbeddingInput(text="a lake")]
    assert await core.embed_multimodal(docs) == [[1, 2, 3]]
    client.aembed_multimodal.assert_awaited_once_with(docs)

    monkeypatch.setenv("EMBED_PROVIDER", "gemma-multimodal")
    monkeypatch.setenv("EMBED_MODEL", "mm-alias")
    monkeypatch.setenv("EMBED_DIMENSIONS", "3")
    assert embedding_dimensions() == 3


def test_unconfigured_multimodal_profile_does_not_borrow_openai_defaults(monkeypatch):
    from core.llm_provider_management import default_embed_for
    from utils.embedding_config import environment_embedding_selection
    monkeypatch.setattr("utils.embedding_config.EMBEDDING_CONFIG", {"active": "mm", "models": {
        "mm": {"provider": "gemma-multimodal", "model": "mm-alias", "dimensions": None}}})
    for key in ("EMBED_PROFILE", "EMBED_PROVIDER", "EMBED_DIMENSIONS", "EMBED_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="configured model and dimensions"):
        environment_embedding_selection()
    with pytest.raises(ValueError, match="configured model and dimensions"):
        default_embed_for("gemma-multimodal")


def test_legacy_text_profile_still_only_controls_prefixes(monkeypatch):
    from utils.embedding_config import environment_embedding_selection, format_query
    monkeypatch.setattr("utils.embedding_config.EMBEDDING_CONFIG", {"active": "gemma", "models": {
        "gemma": {"provider": "openai", "model": "embeddinggemma:300m", "dimensions": 768,
                  "query_prefix": "task: search result | query: {text}"}}})
    for key in ("EMBED_PROFILE", "EMBED_PROVIDER", "EMBED_DIMENSIONS", "EMBED_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "voyage")
    selected = environment_embedding_selection()
    assert selected["provider"] == "voyage"
    assert selected["model"] == "voyage-4"
    assert selected["dimensions"] == 1024
    assert format_query("lake") == "task: search result | query: lake"


_DIMS_ERROR = "^embedding dimensions must be a configured positive integer$"


@pytest.mark.parametrize("dims", ["abc", "3.5", "1e3", True, 3.5, -2, [3]])
def test_nonnumeric_dimensions_raise_consistent_config_error(selection, dims, monkeypatch):
    from utils.embedding_config import environment_embedding_selection
    monkeypatch.setattr("core.proxy.ssrf_safety._safe_async_client",
                        MagicMock(side_effect=AssertionError("must remain lazy")))
    with pytest.raises(ValueError, match=_DIMS_ERROR):
        core._effective_selection({**selection, "dimensions": dims})
    if isinstance(dims, str):
        for provider in ("gemma-multimodal", "openai"):
            monkeypatch.setenv("EMBED_PROVIDER", provider)
            monkeypatch.setenv("EMBED_MODEL", "mm-alias")
            monkeypatch.setenv("EMBED_DIMENSIONS", dims)
            with pytest.raises(ValueError, match=_DIMS_ERROR):
                environment_embedding_selection()
        # Text providers keep legacy int() parsing but share the sanitized conversion message.
        with pytest.raises(ValueError, match=_DIMS_ERROR):
            core._effective_selection({"provider": "openai", "model": "m", "dimensions": dims})
    assert core._effective_selection({**selection, "dimensions": "3"})["dimensions"] == 3
