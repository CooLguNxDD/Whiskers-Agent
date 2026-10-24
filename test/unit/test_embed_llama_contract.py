"""Offline contract: GemmaMultimodalEmbeddings vs llama-server's real /v1/embeddings shape.

An in-process fake llama-server (httpx.MockTransport) stands in for the S01 `embed` sidecar:
no network, no docker. Live behaviour is checked by scripts/smoke_embed_multimodal.py.
"""

import base64
import contextlib
import hashlib
import io
import json
import logging
import math
import random
import struct
import wave

import httpx
import pytest
from PIL import Image

from db_layer.embeddings import embeddings_core as core
from db_layer.embeddings.multimodal import (
    EmbeddingInput, EmbeddingMedia, GemmaMultimodalEmbeddings, MultimodalEmbeddingError)

WIDTH = 768
BASE = "http://embed:8080/v1"
QUERY = "task: search result | query: "
DOC = "title: none | text: "


def png_bytes(color=(200, 30, 30)):
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, format="PNG")
    return stream.getvalue()


def wav_bytes(rate=16000, seconds=0.1, freq=440.0):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        frames = int(rate * seconds)
        audio.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(frames)))
    return stream.getvalue()


def vector_for(parts, scale=1.0, width=WIDTH):
    """Deterministic per-document vector (unit length times `scale`), like a real encoder's output."""
    seed = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).digest()
    rng = random.Random(seed)
    raw = [rng.uniform(-1, 1) for _ in range(width)]
    norm = math.sqrt(sum(v * v for v in raw))
    return [v / norm * scale for v in raw]


class FakeLlamaServer:
    """Mimics llama-server: /v1/embeddings accepts content parts; /embedding is the legacy route."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.shuffle = False
        self.scale = 1.0
        self.width = WIDTH
        self.fail_status: int | None = None
        self.bad_vector: list[float] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/embedding"):
            return httpx.Response(404, json={"error": {
                "code": 404, "message": "File Not Found", "type": "not_found_error"}})
        if path != "/v1/embeddings":
            return httpx.Response(404, json={"error": {"code": 404, "message": "File Not Found"}})
        if self.fail_status:
            kind = "invalid_request_error" if self.fail_status < 500 else "server_error"
            return httpx.Response(self.fail_status, json={"error": {
                "code": self.fail_status, "type": kind,
                "message": "LEAKY-LLAMA-DETAIL input: prompt echo /models/secret.gguf"}})
        body = json.loads(request.content)
        documents = body.get("input")
        if not isinstance(documents, list) or not all(
                isinstance(d, dict) and isinstance(d.get("content"), list) for d in documents):
            return httpx.Response(400, json={"error": {
                "code": 400, "message": "input must be content parts", "type": "invalid_request_error"}})
        data = [{"object": "embedding", "index": i,
                 "embedding": self.bad_vector or vector_for(d["content"], self.scale, self.width)}
                for i, d in enumerate(documents)]
        if self.shuffle:
            data.reverse()
        return httpx.Response(200, json={
            "object": "list", "data": data, "model": body.get("model"),
            "usage": {"prompt_tokens": 7, "total_tokens": 7}})

    def bodies(self):
        return [json.loads(r.content) for r in self.requests]

    def parts(self, call=0, doc=0):
        return self.bodies()[call]["input"][doc]["content"]


@pytest.fixture
def server(monkeypatch):
    fake = FakeLlamaServer()

    @contextlib.asynccontextmanager
    async def safe_client(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as client:
            yield client

    async def safe_url(_url):
        return True

    monkeypatch.setattr("core.proxy.ssrf_safety._safe_async_client", safe_client)
    monkeypatch.setattr("core.proxy.ssrf_safety._is_safe_url", safe_url)
    monkeypatch.delenv("EMBED_TASK_PREFIXES", raising=False)
    monkeypatch.setattr(core, "_model_clients", {})
    return fake


@pytest.fixture
def client():
    return GemmaMultimodalEmbeddings("embeddinggemma-2", WIDTH, None, BASE, batch_size=4)


def norm(vector):
    return math.sqrt(sum(v * v for v in vector))


@pytest.mark.asyncio
async def test_text_only_wire_shape_and_vector(server, client):
    [vector] = await client.aembed_multimodal([EmbeddingInput(text="a stone bridge")])
    request = server.requests[0]
    assert (request.method, request.url.path) == ("POST", "/v1/embeddings")
    body = server.bodies()[0]
    assert set(body) == {"model", "dimensions", "encoding_format", "input"}
    assert body["model"] == "embeddinggemma-2" and body["dimensions"] == WIDTH
    assert body["encoding_format"] == "float"
    assert body["input"] == [{"content": [{"type": "text", "text": DOC + "a stone bridge"}]}]
    assert len(vector) == WIDTH and abs(norm(vector) - 1) <= 1e-2


@pytest.mark.asyncio
async def test_png_is_inline_image_part_without_prefix(server, client):
    raw = png_bytes()
    [vector] = await client.aembed_multimodal([EmbeddingInput(media=EmbeddingMedia(raw, "image/png"))])
    [part] = server.parts()
    assert part["type"] == "image_url"
    head, encoded = part["image_url"]["url"].split(",", 1)
    assert head == "data:image/png;base64" and base64.b64decode(encoded) == raw
    assert len(vector) == WIDTH


@pytest.mark.asyncio
async def test_wav_16khz_mono_is_input_audio_part(server, client):
    raw = wav_bytes()
    with wave.open(io.BytesIO(raw)) as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
    [vector] = await client.aembed_multimodal([EmbeddingInput(media=EmbeddingMedia(raw, "audio/wav"))])
    [part] = server.parts()
    assert part["type"] == "input_audio" and part["input_audio"]["format"] == "wav"
    assert base64.b64decode(part["input_audio"]["data"]) == raw
    assert len(vector) == WIDTH


@pytest.mark.asyncio
async def test_joint_text_image_document_is_one_input_item(server, client):
    raw = png_bytes()
    vectors = await client.aembed_multimodal([
        EmbeddingInput(text="a red square", media=EmbeddingMedia(raw, "image/png"))])
    assert len(vectors) == 1 and len(server.bodies()[0]["input"]) == 1
    text_part, image_part = server.parts()
    assert text_part == {"type": "text", "text": DOC + "a red square"}
    assert image_part["type"] == "image_url"


@pytest.mark.asyncio
async def test_prefix_applies_to_text_parts_only(server, client):
    await client.aembed_multimodal([
        EmbeddingInput(text="caption", media=EmbeddingMedia(png_bytes(), "image/png")),
        EmbeddingInput(text="river", media=EmbeddingMedia(wav_bytes(), "audio/wav")),
        EmbeddingInput(media=EmbeddingMedia(png_bytes((0, 0, 200)), "image/png")),
    ])
    docs = server.bodies()[0]["input"]
    for doc in docs:
        for part in doc["content"]:
            if part["type"] == "text":
                assert part["text"].startswith(DOC)
            elif part["type"] == "image_url":
                assert part["image_url"]["url"].startswith("data:image/png;base64,")
                assert "task:" not in part["image_url"]["url"][:22] and "title:" not in part["image_url"]["url"][:22]
            else:
                assert set(part) == {"type", "input_audio"} and set(part["input_audio"]) == {"data", "format"}
    assert [p["type"] for p in docs[2]["content"]] == ["image_url"]


@pytest.mark.asyncio
async def test_query_vs_document_prefix_title_and_idempotence(server, client):
    await client.aembed_query("find bridges")
    await client.aembed_documents(["about bridges"])
    await client.aembed_documents(["about lakes"], titles=["Lake  notes"])
    await client.aembed_query(QUERY + "already prefixed")
    await client.aembed_documents([DOC + "already prefixed", "title: T | text: kept"])
    texts = [server.parts(i)[0]["text"] for i in range(5)]
    assert texts[0] == QUERY + "find bridges"
    assert texts[1] == DOC + "about bridges"
    assert texts[2] == "title: Lake notes | text: about lakes"
    assert texts[3] == QUERY + "already prefixed"
    assert [d["content"][0]["text"] for d in server.bodies()[4]["input"]] == [
        DOC + "already prefixed", "title: T | text: kept"]


@pytest.mark.asyncio
async def test_core_titles_feed_document_prefix_and_queries_get_query_prefix(server):
    sel = {"provider": "gemma-multimodal", "model": "embeddinggemma-2", "dimensions": WIDTH, "base_url": BASE}
    await core.embed_documents_with(sel, ["body one", "body two"], titles=["One", None])
    await core.embed_query_with(sel, "what is it")
    docs = server.bodies()[0]["input"]
    assert [d["content"][0]["text"] for d in docs] == [
        "title: One | text: body one", DOC + "body two"]
    assert server.parts(1)[0]["text"] == QUERY + "what is it"


@pytest.mark.asyncio
async def test_prefixes_can_be_disabled_by_arg_or_env(server, monkeypatch):
    off = GemmaMultimodalEmbeddings("m", WIDTH, None, BASE, task_prefixes=False)
    await off.aembed_query("raw q")
    await off.aembed_documents(["raw d"])
    assert [server.parts(i)[0]["text"] for i in range(2)] == ["raw q", "raw d"]
    monkeypatch.setenv("EMBED_TASK_PREFIXES", "0")
    env_off = GemmaMultimodalEmbeddings("m", WIDTH, None, BASE)
    assert env_off.task_prefixes is False
    monkeypatch.setenv("EMBED_TASK_PREFIXES", "1")
    assert GemmaMultimodalEmbeddings("m", WIDTH, None, BASE).task_prefixes is True
    assert GemmaMultimodalEmbeddings("m", WIDTH, None, BASE, task_prefixes=False).task_prefixes is False


@pytest.mark.asyncio
async def test_out_of_order_indices_are_resorted(server, client):
    server.shuffle = True
    docs = [EmbeddingInput(text=t) for t in ("alpha", "beta", "gamma")]
    vectors = await client.aembed_multimodal(docs)
    assert server.requests and len(vectors) == 3
    for text, vector in zip(("alpha", "beta", "gamma"), vectors):
        expect = vector_for([{"type": "text", "text": DOC + text}])
        assert vector == pytest.approx(expect)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 500])
async def test_http_errors_are_sanitized(status, server, client, caplog):
    server.fail_status = status
    with caplog.at_level(logging.DEBUG, logger="whiskers"):
        with pytest.raises(MultimodalEmbeddingError) as failure:
            await client.aembed_multimodal([EmbeddingInput(text="private caption")])
    assert failure.value.category == "http_status" and failure.value.status == status
    shown = str(failure.value) + caplog.text
    for leaked in ("LEAKY-LLAMA-DETAIL", "secret.gguf", "private caption", "embed:8080", "prompt echo"):
        assert leaked not in shown


@pytest.mark.asyncio
async def test_unnormalized_vectors_are_normalized_client_side(server, client):
    server.scale = 37.5
    vectors = await client.aembed_multimodal([EmbeddingInput(text="loud"), EmbeddingInput(text="louder")])
    for text, vector in zip(("loud", "louder"), vectors):
        assert abs(norm(vector) - 1) <= 1e-6
        assert vector == pytest.approx(vector_for([{"type": "text", "text": DOC + text}]), abs=1e-6)


@pytest.mark.asyncio
async def test_near_unit_vectors_pass_through_and_zero_vector_is_rejected(server, client):
    server.scale = 1.005
    [vector] = await client.aembed_multimodal([EmbeddingInput(text="x")])
    assert vector == pytest.approx(vector_for([{"type": "text", "text": DOC + "x"}], 1.005))
    server.bad_vector = [0.0] * WIDTH
    with pytest.raises(ValueError, match="response"):
        await client.aembed_multimodal([EmbeddingInput(text="x")])


@pytest.mark.asyncio
async def test_wrong_width_is_an_error(server, client):
    server.width = 512
    with pytest.raises(ValueError, match="dimension"):
        await client.aembed_multimodal([EmbeddingInput(text="x")])


@pytest.mark.asyncio
async def test_legacy_embedding_route_is_never_used(server, client):
    for bad in ("http://embed:8080/embedding", "http://embed:8080/v1/embeddings", "http://embed:8080/v1/embedding/"):
        with pytest.raises(ValueError, match="API base"):
            GemmaMultimodalEmbeddings("m", WIDTH, None, bad)
    await client.aembed_multimodal([
        EmbeddingInput(text="t"),
        EmbeddingInput(text="c", media=EmbeddingMedia(png_bytes(), "image/png")),
        EmbeddingInput(media=EmbeddingMedia(wav_bytes(), "audio/wav"))])
    await client.aembed_query("q")
    assert server.requests
    for request in server.requests:
        assert request.url.path == "/v1/embeddings" and not request.url.path.endswith("/embedding")
        assert b"image_data" not in request.content
    # The fake mimics llama.cpp: the legacy route 404s (and silently drops images when it exists).
    async with httpx.AsyncClient(transport=httpx.MockTransport(server.handler)) as probe:
        assert (await probe.post("http://embed:8080/embedding", json={})).status_code == 404
