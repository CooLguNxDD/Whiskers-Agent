"""Offline contract tests for the configured LiteLLM content-parts embedding route."""

import asyncio
import base64
import io
import json
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from db_layer.embeddings import embeddings_core as core
from db_layer.embeddings.multimodal import EmbeddingInput, EmbeddingMedia
from utils.embedding_config import selection_for_profile


@pytest.fixture
def selection():
    return {
        "provider": "gemma-multimodal", "model": "test-multimodal-alias",
        "dimensions": 3, "base_url": "https://gateway.example.invalid/v1",
        "batch_size": 2, "max_concurrency": 2,
    }


@pytest.fixture
def transport(monkeypatch):
    client = MagicMock()
    client.post = AsyncMock()

    async def respond(_url, *, json, headers):
        response = MagicMock()
        response.json.return_value = {
            "data": [{"index": i, "embedding": [i + 0.1, 0.2, 0.3]}
                     for i in reversed(range(len(json["input"])))]
        }
        return response

    client.post.side_effect = respond
    client.contexts = []

    def open_client(**_kwargs):
        # A fresh context per client creation so tests can count/inspect enter and exit.
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=client)
        context.__aexit__ = AsyncMock(return_value=None)
        client.contexts.append(context)
        return context

    factory = MagicMock(side_effect=open_client)
    monkeypatch.setattr("core.proxy.ssrf_safety._safe_async_client", factory)
    monkeypatch.setattr("core.proxy.ssrf_safety._is_safe_url", AsyncMock(return_value=True))
    monkeypatch.setattr(core, "_model_clients", {})
    return client


def image_bytes(fmt):
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), (20, 50, 80)).save(stream, format=fmt)
    return stream.getvalue()


def wav_bytes():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 16)
    return stream.getvalue()


@pytest.mark.asyncio
async def test_text_uses_configured_endpoint_model_dimensions(selection, transport):
    vectors = await core.embed_multimodal_with(selection, [EmbeddingInput(text="a bridge")])
    assert vectors == [[0.1, 0.2, 0.3]]
    call = transport.post.call_args
    assert call.args == ("https://gateway.example.invalid/v1/embeddings",)
    assert call.kwargs["json"] == {
        "model": "test-multimodal-alias", "dimensions": 3, "encoding_format": "float",
        "input": [{"content": [{"type": "text", "text": "a bridge"}]}],
    }
    assert call.kwargs["headers"] == {}
    from core.proxy.ssrf_safety import _safe_async_client
    _safe_async_client.assert_called_once_with(timeout=60, follow_redirects=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
async def test_png_and_jpeg_are_actual_media_content(fmt, mime, selection, transport):
    raw = image_bytes(fmt)
    result = await core.embed_multimodal_with(selection, [
        EmbeddingInput(text="reference bridge", media=EmbeddingMedia(raw, mime))])
    assert result == [[0.1, 0.2, 0.3]]
    parts = transport.post.call_args.kwargs["json"]["input"][0]["content"]
    assert parts[0] == {"type": "text", "text": "reference bridge"}
    assert parts[1]["type"] == "image_url"
    prefix, encoded = parts[1]["image_url"]["url"].split(",", 1)
    assert prefix == f"data:{mime};base64"
    assert base64.b64decode(encoded) == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [None, "river ambience"])
async def test_wav_is_actual_audio_content(text, selection, transport):
    raw = wav_bytes()
    result = await core.embed_multimodal_with(selection, [
        EmbeddingInput(text=text, media=EmbeddingMedia(raw, "audio/wav"))])
    assert result == [[0.1, 0.2, 0.3]]
    parts = transport.post.call_args.kwargs["json"]["input"][0]["content"]
    assert len(parts) == (2 if text else 1)
    if text:
        assert parts[0] == {"type": "text", "text": text}
    assert parts[-1]["type"] == "input_audio"
    assert parts[-1]["input_audio"]["format"] == "wav"
    assert base64.b64decode(parts[-1]["input_audio"]["data"]) == raw


@pytest.mark.asyncio
async def test_batch_splitting_indexes_order_concurrency_and_empty(selection, transport):
    active = peak = 0

    async def respond(_url, *, json, headers):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        values = [int(item["content"][0]["text"]) for item in json["input"]]
        await asyncio.sleep(0)
        active -= 1
        response = MagicMock()
        response.json.return_value = {"data": [
            {"index": i, "embedding": [values[i], 1, 2]} for i in reversed(range(len(values)))]}
        return response

    transport.post.side_effect = respond
    assert await core.embed_multimodal_with(selection, []) == []
    transport.post.assert_not_called()
    docs = [EmbeddingInput(text=str(i)) for i in range(7)]
    assert await core.embed_multimodal_with(selection, docs) == [[i, 1, 2] for i in range(7)]
    assert [len(c.kwargs["json"]["input"]) for c in transport.post.call_args_list] == [2, 2, 2, 1]
    assert peak == 2


@pytest.mark.asyncio
async def test_registry_identity_profile_and_text_compatibility(selection, transport, monkeypatch):
    from core.llm_provider_management import get_llm_provider_registry
    spec = get_llm_provider_registry().get("gemma-multimodal")
    assert spec.embedding_modalities == ("text", "image/png", "image/jpeg", "audio/wav")
    assert core.model_id_for(selection) == "gemma-multimodal:test-multimodal-alias:3"
    client = await core._get_client_for(selection)
    assert await core._get_client_for(dict(selection)) is client
    assert client.dimensions == 3
    monkeypatch.setattr("utils.embedding_config.get_active_profile", lambda: {})
    assert await core.embed_query_with(selection, "bridge") == [0.1, 0.2, 0.3]
    assert await core.embed_documents_with(selection, ["bridge", "lake"]) == [[0.1, 0.2, 0.3], [1.1, 0.2, 0.3]]
    monkeypatch.setattr(core, "_get_embeddings_async", AsyncMock(return_value=client))
    assert await core.embed("bridge") == [0.1, 0.2, 0.3]
    assert await core.embed_batch(["bridge"]) == [[0.1, 0.2, 0.3]]

    profiles = json.loads(Path("config/embedding_config_example.json").read_text())["models"]
    monkeypatch.setattr("utils.embedding_config.EMBEDDING_CONFIG", {"models": profiles})
    resolved = selection_for_profile("gemma_multimodal")
    assert resolved["provider"] == "gemma-multimodal"
    assert resolved["batch_size"] > 0
    assert profiles["gemma"]["model"] == "embeddinggemma:300m"
    assert profiles["gemma"]["modalities"] == ["text"]

    legacy = MagicMock()
    legacy.aembed_query = AsyncMock(return_value=[4, 5])
    legacy.aembed_documents = AsyncMock(return_value=[[4, 5]])
    monkeypatch.setattr("core.llm_provider_management.make_embeddings", lambda **kw: legacy)
    other = {"provider": "voyage", "model": "voyage-test", "dimensions": 2}
    assert await core.embed_query_with(other, "plain") == [4, 5]
    assert await core.embed_documents_with(other, ["plain"]) == [[4, 5]]
    calls = transport.post.call_count
    with pytest.raises(ValueError, match="multimodal"):
        await core.embed_multimodal_with(other, [EmbeddingInput(media=EmbeddingMedia(wav_bytes(), "audio/wav"))])
    assert transport.post.call_count == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("media", [
    (b"not png", "image/png"), (b"\xff\xd8\xff\xd9", "image/jpeg"),
    (b"RIFF\0\0\0\0WAVE", "audio/wav"), (b"GIF89a", "image/gif"),
    ("binary is not a string", "image/png"), (b"", "audio/wav"),
])
async def test_invalid_media_rejected_before_io(media, selection, transport):
    with pytest.raises((TypeError, ValueError)):
        await core.embed_multimodal_with(selection, [
            EmbeddingInput(text="valid preceding document"),
            EmbeddingInput(media=EmbeddingMedia(*media)),
        ])
    transport.post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    [], [{"index": 0, "embedding": [1, 2]}],
    [{"index": 0, "embedding": [1, float("nan"), 3]}],
    [{"index": 0, "embedding": [1, float("inf"), 3]}],
    [{"index": 0, "embedding": [1, "2", 3]}],
    [{"index": 0, "embedding": [1, True, 3]}],
    [{"index": 1, "embedding": [1, 2, 3]}],
    [{"embedding": [1, 2, 3]}], None,
])
async def test_malformed_provider_response_fails(data, selection, transport):
    response = MagicMock()
    response.json.return_value = {"data": data}
    transport.post.side_effect = None
    transport.post.return_value = response
    with pytest.raises(ValueError, match="response"):
        await core.embed_multimodal_with(selection, [EmbeddingInput(text="x")])


@pytest.mark.asyncio
async def test_duplicate_indexes_and_failed_chunk_never_return_partial_success(selection, transport):
    response = MagicMock()
    response.json.return_value = {"data": [{"index": 0, "embedding": [1, 2, 3]}] * 2}
    transport.post.side_effect = None
    transport.post.return_value = response
    with pytest.raises(ValueError, match="response"):
        await core.embed_multimodal_with(selection, [EmbeddingInput(text="x"), EmbeddingInput(text="y")])

    valid = MagicMock()
    valid.json.return_value = {"data": [
        {"index": 0, "embedding": [1, 2, 3]}, {"index": 1, "embedding": [4, 5, 6]}]}
    transport.post.side_effect = [valid, RuntimeError("provider failure")]
    with pytest.raises(ValueError, match="request/response failed"):
        await core.embed_multimodal_with(selection, [EmbeddingInput(text="x")] * 3)
    assert transport.post.call_count == 3


@pytest.mark.asyncio
async def test_configuration_change_does_not_reuse_wrong_endpoint(selection, transport):
    first = await core._get_client_for(selection)
    second = await core._get_client_for({**selection, "base_url": "https://other.example.invalid/v1", "batch_size": 1})
    assert second is not first
    assert core.model_id_for(selection) == core.model_id_for({**selection, "base_url": "https://other.example.invalid/v1"})
    await core.embed_multimodal_with({**selection, "base_url": "https://other.example.invalid/v1"}, [EmbeddingInput(text="x")])
    assert transport.post.call_args.args[0] == "https://other.example.invalid/v1/embeddings"


@pytest.mark.asyncio
async def test_opt_in_profile_resolution_and_environment_overrides(selection, monkeypatch, transport):
    from core.llm_config_service import _env_embedding
    monkeypatch.setattr("utils.embedding_config.EMBEDDING_CONFIG", {"active": "mm", "models": {"mm": selection}})
    for key in ("EMBED_PROVIDER", "EMBED_MODEL", "EMBED_DIMENSIONS", "EMBED_BASE_URL", "EMBED_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    resolved = _env_embedding()
    assert resolved["provider"] == selection["provider"]
    assert resolved["model"] == selection["model"]
    assert resolved["dimensions"] == 3
    assert resolved["batch_size"] == 2
    monkeypatch.setenv("EMBED_DIMENSIONS", "4")
    monkeypatch.setenv("EMBED_BASE_URL", "https://override.example.invalid/v1")
    assert _env_embedding()["dimensions"] == 4
    assert _env_embedding()["base_url"] == "https://override.example.invalid/v1"
    from core.embedding_dimensions import embedding_dimensions
    assert embedding_dimensions() == 4


@pytest.mark.asyncio
async def test_empty_global_api_and_invalid_document_types(selection, transport, monkeypatch):
    resolver = AsyncMock(side_effect=AssertionError("empty must not resolve"))
    monkeypatch.setattr(core, "_get_embeddings_async", resolver)
    assert await core.embed_multimodal([]) == []
    for value in (EmbeddingInput(), EmbeddingInput(text=b"bytes"), "not a typed document",
                  EmbeddingInput(media=EmbeddingMedia(image_bytes("PNG"), "image/jpeg")),
                  EmbeddingInput(media=EmbeddingMedia(wav_bytes()[:-2], "audio/wav"))):
        with pytest.raises((TypeError, ValueError)):
            await core.embed_multimodal_with(selection, [value])
    transport.post.assert_not_called()


@pytest.mark.asyncio
async def test_unsafe_endpoint_and_transport_errors_are_sanitized(selection, transport, monkeypatch):
    monkeypatch.setattr("core.proxy.ssrf_safety._is_safe_url", AsyncMock(return_value=False))
    with pytest.raises(ValueError, match="request/response failed"):
        await core.embed_multimodal_with(selection, [EmbeddingInput(text="x")])
    transport.post.assert_not_called()
    monkeypatch.setattr("core.proxy.ssrf_safety._is_safe_url", AsyncMock(return_value=True))
    transport.post.side_effect = RuntimeError("provider echoed private request contents")
    with pytest.raises(ValueError) as failure:
        await core.embed_multimodal_with(selection, [EmbeddingInput(text="x")])
    assert "private request" not in str(failure.value)
    assert failure.value.__suppress_context__


@pytest.mark.asyncio
async def test_serialization_is_windowed_released_and_later_invalid_media_precedes_io(
        selection, transport, monkeypatch):
    import gc
    import weakref
    from db_layer.embeddings import multimodal

    class Window(list):
        """Weak-referenceable list so the test can observe release of encoded payloads."""

    original, sizes, released = multimodal._serialize, [], []

    def spy(inputs):
        sizes.append(len(inputs))
        window = Window(original(inputs))
        released.append(weakref.ref(window))
        return window

    monkeypatch.setattr(multimodal, "_serialize", spy)
    active = peak = 0

    async def respond(_url, *, json, headers):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        gc.collect()
        # Encoded payloads of every earlier window are gone once a later window is in flight.
        assert all(ref() is None for ref in released[:-1])
        await asyncio.sleep(0)
        active -= 1
        values = [int(item["content"][0]["text"]) for item in json["input"]]
        response = MagicMock()
        response.json.return_value = {"data": [
            {"index": i, "embedding": [values[i], 0, 0]} for i in reversed(range(len(values)))]}
        return response

    transport.post.side_effect = respond
    png = EmbeddingMedia(image_bytes("PNG"), "image/png")
    docs = [EmbeddingInput(text=str(i), media=png if i % 3 == 0 else None) for i in range(9)]
    assert await core.embed_multimodal_with(selection, docs) == [[i, 0, 0] for i in range(9)]
    # batch_size=2 x max_concurrency=2: serialization never sees more than one 4-document window.
    assert sizes == [4, 4, 1]
    assert [len(c.kwargs["json"]["input"]) for c in transport.post.call_args_list] == [2, 2, 2, 2, 1]
    assert peak == 2
    sent = transport.post.call_args_list[0].kwargs["json"]["input"][0]["content"][1]
    assert base64.b64decode(sent["image_url"]["url"].split(",", 1)[1]) == png.data

    sizes.clear()
    transport.post.reset_mock()
    from core.proxy.ssrf_safety import _safe_async_client
    _safe_async_client.reset_mock()
    bad = docs[:8] + [EmbeddingInput(media=EmbeddingMedia(b"\x89PNG truncated", "image/png"))]
    with pytest.raises(ValueError, match="PNG/JPEG"):
        await core.embed_multimodal_with(selection, bad)
    # Third-window media fails preflight: no window encoded, no client opened, no request sent.
    assert sizes == []
    _safe_async_client.assert_not_called()
    transport.post.assert_not_called()


@pytest.mark.asyncio
async def test_one_safe_client_per_invocation_with_cleanup(selection, transport):
    import httpx
    from core.proxy.ssrf_safety import _safe_async_client
    from db_layer.embeddings.multimodal import MultimodalEmbeddingError
    docs = [EmbeddingInput(text=str(i)) for i in range(7)]

    await core.embed_multimodal_with(selection, docs)
    assert transport.post.call_count == 4
    assert _safe_async_client.call_count == 1
    _safe_async_client.assert_called_with(timeout=60, follow_redirects=False)
    transport.contexts[0].__aexit__.assert_awaited_once()

    first, second = await asyncio.gather(
        core.embed_multimodal_with(selection, docs[:3]), core.embed_multimodal_with(selection, docs[3:]))
    assert first == [[i + 0.1, 0.2, 0.3] for i in range(2)] + [[0.1, 0.2, 0.3]]
    assert len(second) == 4
    assert len(transport.contexts) == 3
    for context in transport.contexts[1:]:
        context.__aenter__.assert_awaited_once()
        context.__aexit__.assert_awaited_once()

    transport.post.side_effect = httpx.ConnectError("connection refused")
    with pytest.raises(MultimodalEmbeddingError) as failure:
        await core.embed_multimodal_with(selection, docs)
    assert failure.value.category == "transport"
    transport.contexts[-1].__aexit__.assert_awaited_once()
    # AsyncExitStack calls type(cm).__aexit__(cm, exc_type, exc, tb); the failure reaches the client exit.
    assert MultimodalEmbeddingError in transport.contexts[-1].__aexit__.await_args.args

    started = asyncio.Event()

    async def hang(_url, *, json, headers):
        started.set()
        await asyncio.Event().wait()

    transport.post.side_effect = hang
    task = asyncio.create_task(core.embed_multimodal_with(selection, docs))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    transport.contexts[-1].__aexit__.assert_awaited_once()
    assert asyncio.CancelledError in transport.contexts[-1].__aexit__.await_args.args


@pytest.mark.asyncio
async def test_failure_categories_logged_without_secrets(selection, transport, caplog):
    import logging
    import httpx
    from db_layer.embeddings.multimodal import MultimodalEmbeddingError
    secret = "synthetic-secret-token-0000"
    configured = {**selection, "api_key": secret, "base_url": "https://secret-host.example.invalid/v1"}
    request = httpx.Request("POST", "https://secret-host.example.invalid/v1/embeddings")

    def status_failure():
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"bad {secret}", request=request, response=httpx.Response(503, text=secret, request=request))
        return response

    def bad_json():
        response = MagicMock()
        response.json.side_effect = ValueError(f"not json {secret}")
        return response

    cases = [
        (httpx.ReadTimeout(f"timed out {secret}", request=request), "timeout", None),
        (status_failure(), "http_status", 503),
        (bad_json(), "invalid_json", None),
        (RuntimeError(f"echo {secret}"), "transport", None),
    ]
    caplog.set_level(logging.WARNING, logger="whiskers")
    for outcome, category, status in cases:
        transport.post.side_effect = None
        if isinstance(outcome, BaseException):
            transport.post.side_effect = outcome
        else:
            transport.post.return_value = outcome
        with pytest.raises(MultimodalEmbeddingError) as failure:
            await core.embed_multimodal_with(configured, [EmbeddingInput(text="x")])
        assert (failure.value.category, failure.value.status) == (category, status)
        assert "request/response failed" in str(failure.value)
        assert secret not in str(failure.value)
        assert failure.value.__suppress_context__
        assert f"category={category} status={status} batch_size=1" in caplog.text

    malformed = MagicMock()
    malformed.json.return_value = {"data": [{"index": 0, "embedding": [1, 2]}]}
    transport.post.side_effect = None
    transport.post.return_value = malformed
    with pytest.raises(ValueError, match="dimension mismatch"):
        await core.embed_multimodal_with(configured, [EmbeddingInput(text="x")])
    assert "category=invalid_response" in caplog.text
    for leaked in (secret, "secret-host", "Bearer", "Traceback"):
        assert leaked not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
