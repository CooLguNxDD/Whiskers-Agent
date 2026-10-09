"""Typed documents and async LiteLLM content-parts embeddings (adapter v1).

A document carries text, one reference PNG/JPEG or PCM WAV, or both. Media is
inline content, never a path/URL or a binary-to-string fallback. See the serving
contract in docs/gemma-multimodal-embedding.md; stock /embeddings is insufficient.
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
import warnings
import wave
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from langchain_core.embeddings import Embeddings


@dataclass(frozen=True)
class EmbeddingMedia:
    """Reference media bytes with an exact image/png, image/jpeg or audio/wav MIME."""

    data: bytes = field(repr=False)
    mime_type: str


@dataclass(frozen=True)
class EmbeddingInput:
    """One joint embedding: nonempty text, one reference media item, or both."""

    text: str | None = None
    media: EmbeddingMedia | None = None


def _positive_int(value: object, name: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be a positive integer within the supported limit")
    return value


def _validate_media(media: EmbeddingMedia, max_bytes: int) -> None:
    if not isinstance(media, EmbeddingMedia) or type(media.data) is not bytes:
        raise TypeError("media must contain bytes and a supported MIME type")
    if not media.data or len(media.data) > max_bytes:
        raise ValueError("media is empty or exceeds max_media_bytes")
    if media.mime_type in ("image/png", "image/jpeg"):
        from PIL import Image
        expected = {"image/png": "PNG", "image/jpeg": "JPEG"}[media.mime_type]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(media.data)) as image:
                    if image.format != expected:
                        raise ValueError("image MIME does not match content")
                    image.verify()
                # verify() alone does not decode JPEG scan data; reject truncated scans too.
                with Image.open(io.BytesIO(media.data)) as image:
                    image.load()
        except Exception:
            raise ValueError("invalid PNG/JPEG media or MIME mismatch") from None
    elif media.mime_type == "audio/wav":
        try:
            with wave.open(io.BytesIO(media.data), "rb") as audio:
                frames = audio.getnframes()
                frame_size = audio.getnchannels() * audio.getsampwidth()
                if (audio.getcomptype() != "NONE" or frames < 1 or audio.getframerate() < 1
                        or len(audio.readframes(frames)) != frames * frame_size):
                    raise ValueError("invalid PCM WAV")
        except Exception:
            raise ValueError("invalid or unsupported PCM WAV media") from None
    else:
        raise ValueError("unsupported media MIME; use image/png, image/jpeg or audio/wav")


def _serialize(inputs: list[EmbeddingInput], max_bytes: int) -> list[dict]:
    documents = []
    for document in inputs:
        if not isinstance(document, EmbeddingInput):
            raise TypeError("multimodal input must be an EmbeddingInput")
        if document.text is not None and not isinstance(document.text, str):
            raise TypeError("embedding text must be a string, never bytes")
        if not (document.text and document.text.strip()) and document.media is None:
            raise ValueError("embedding input requires nonempty text or media")
        parts = []
        if document.text:
            parts.append({"type": "text", "text": document.text})
        if document.media is not None:
            _validate_media(document.media, max_bytes)
            encoded = base64.b64encode(document.media.data).decode("ascii")
            if document.media.mime_type == "audio/wav":
                parts.append({"type": "input_audio", "input_audio": {"data": encoded, "format": "wav"}})
            else:
                url = f"data:{document.media.mime_type};base64,{encoded}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
        documents.append({"content": parts})
    return documents


def _vectors(response: object, count: int, dimensions: int) -> list[list[float]]:
    """Require a complete indexed permutation and finite numeric vectors of exact width."""
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list) or len(data) != count:
        raise ValueError("embedding response cardinality mismatch")
    ordered: dict[int, list[float]] = {}
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("invalid embedding response item")
        index, vector = item.get("index"), item.get("embedding")
        if type(index) is not int or index not in range(count) or index in ordered:
            raise ValueError("invalid or duplicate embedding response index")
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise ValueError("embedding response dimension mismatch")
        try:
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
                raise ValueError("invalid numeric vector")
            ordered[index] = [float(v) for v in vector]
        except (ValueError, OverflowError, TypeError):
            raise ValueError("embedding response contains nonfinite or nonnumeric data") from None
    return [ordered[i] for i in range(count)]


class GemmaMultimodalEmbeddings(Embeddings):
    """Async-only client for an explicitly configured LiteLLM multimodal adapter route."""

    def __init__(self, model: str, dimensions: int, api_key: str | None, base_url: str | None,
                 *, batch_size: int = 8, max_concurrency: int = 2,
                 max_media_bytes: int = 10_000_000, timeout_seconds: int = 60) -> None:
        """Validate configuration without I/O; endpoint/model/width have no transport defaults."""
        if not isinstance(model, str) or not model.strip():
            raise ValueError("multimodal model alias is required")
        parsed = urlsplit(base_url or "")
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("multimodal base_url must be an HTTP(S) endpoint without credentials")
        self.model = model
        self.dimensions = _positive_int(dimensions, "dimensions")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.batch_size = _positive_int(batch_size, "batch_size", 256)
        self.max_concurrency = _positive_int(max_concurrency, "max_concurrency", 32)
        self.max_media_bytes = _positive_int(max_media_bytes, "max_media_bytes", 100_000_000)
        self.timeout_seconds = _positive_int(timeout_seconds, "timeout_seconds", 600)
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    async def _request(self, documents: list[dict]) -> list[list[float]]:
        from core.proxy.ssrf_safety import _is_safe_url, _safe_async_client
        payload = {"model": self.model, "dimensions": self.dimensions,
                   "encoding_format": "float", "input": documents}
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        async with self._semaphore:
            try:
                endpoint = f"{self.base_url}/embeddings"
                if not await _is_safe_url(endpoint):
                    raise ValueError("unsafe embedding endpoint")
                # Re-check and pin DNS at connect time too; redirects stay disabled.
                async with _safe_async_client(timeout=self.timeout_seconds, follow_redirects=False) as client:
                    response = await client.post(f"{self.base_url}/embeddings", json=payload, headers=headers)
                    response.raise_for_status()
                    body = response.json()
            except Exception:
                # Do not leak headers, provider error bodies, inline media or endpoint credentials.
                raise ValueError("multimodal embedding request/response failed; check adapter route") from None
        return _vectors(body, len(documents), self.dimensions)

    async def aembed_multimodal(self, inputs: list[EmbeddingInput]) -> list[list[float]]:
        """Embed complete ordered inputs; bound batch size, tasks and in-flight requests."""
        if not inputs:
            return []
        # Decode/validate ALL media before any outbound call, off the event loop.
        documents = await asyncio.to_thread(_serialize, inputs, self.max_media_bytes)
        vectors = []
        window_size = self.batch_size * self.max_concurrency
        for start in range(0, len(documents), window_size):
            window = documents[start:start + window_size]
            results = await asyncio.gather(*(
                self._request(window[i:i + self.batch_size])
                for i in range(0, len(window), self.batch_size)
            ), return_exceptions=True)
            # Every task in this bounded window is awaited even if one fails.
            for result in results:
                if isinstance(result, BaseException):
                    raise result
                vectors.extend(result)
        return vectors

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Adapt legacy async text batches to the same multimodal route."""
        return await self.aembed_multimodal([EmbeddingInput(text=text) for text in texts])

    async def aembed_query(self, text: str) -> list[float]:
        """Adapt a legacy async text query without tokenization or binary coercion."""
        return (await self.aembed_documents([text]))[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Sync use is unsupported; callers must await aembed_documents."""
        raise NotImplementedError("multimodal embeddings require the async API")

    def embed_query(self, text: str) -> list[float]:
        """Sync use is unsupported; never create a nested/background event loop."""
        raise NotImplementedError("multimodal embeddings require the async API")
