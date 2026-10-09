"""Typed documents and async LiteLLM content-parts embeddings (adapter v1).

A document carries text, one reference PNG/JPEG or PCM WAV, or both. Media is
inline content, never a path/URL or a binary-to-string fallback. See the serving
contract in docs/gemma-multimodal-embedding.md; stock /embeddings is insufficient.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import logging
import math
import warnings
import wave
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from langchain_core.embeddings import Embeddings

logger = logging.getLogger("whiskers")


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


def _validate(inputs: list[EmbeddingInput], max_bytes: int) -> None:
    """Preflight every document (types, MIME, decode) without retaining encoded payloads."""
    for document in inputs:
        if not isinstance(document, EmbeddingInput):
            raise TypeError("multimodal input must be an EmbeddingInput")
        if document.text is not None and not isinstance(document.text, str):
            raise TypeError("embedding text must be a string, never bytes")
        if not (document.text and document.text.strip()) and document.media is None:
            raise ValueError("embedding input requires nonempty text or media")
        if document.media is not None:
            _validate_media(document.media, max_bytes)


def _serialize(inputs: list[EmbeddingInput]) -> list[dict]:
    """Encode one already-validated window into content parts (frozen inputs, immutable bytes)."""
    documents = []
    for document in inputs:
        parts = []
        if document.text:
            parts.append({"type": "text", "text": document.text})
        if document.media is not None:
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


FAILURE_CATEGORIES = frozenset({
    "unsafe_endpoint", "timeout", "http_status", "transport", "invalid_json", "invalid_response"})


class MultimodalEmbeddingError(ValueError):
    """Sanitized adapter failure; `category` is one of FAILURE_CATEGORIES, `status` an HTTP code or None."""

    def __init__(self, category: str, status: int | None = None) -> None:
        detail = f"{category}, HTTP {status}" if status is not None else category
        super().__init__(f"multimodal embedding request/response failed ({detail}); check adapter route")
        self.category = category
        self.status = status


def _log_failure(category: str, batch_size: int, status: int | None = None) -> None:
    # Allowlisted metadata only: never exception text, endpoint, headers, media or provider bodies.
    if category not in FAILURE_CATEGORIES:
        category = "transport"
    logger.warning("multimodal embedding failure category=%s status=%s batch_size=%d",
                   category, status, batch_size)


def _failure(category: str, batch_size: int, status: int | None = None) -> MultimodalEmbeddingError:
    _log_failure(category, batch_size, status)
    return MultimodalEmbeddingError(category if category in FAILURE_CATEGORIES else "transport", status)


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

    async def _request(self, client: object, documents: list[dict]) -> list[list[float]]:
        import httpx
        from core.proxy.ssrf_safety import _is_safe_url
        count = len(documents)
        payload = {"model": self.model, "dimensions": self.dimensions,
                   "encoding_format": "float", "input": documents}
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        endpoint = f"{self.base_url}/embeddings"
        # Errors below never carry headers, provider bodies, inline media or the endpoint.
        async with self._semaphore:
            try:
                safe = await _is_safe_url(endpoint)
            except Exception:
                safe = False
            if not safe:
                raise _failure("unsafe_endpoint", count)
            try:
                # The SSRF transport re-checks and pins DNS at connect time; redirects stay disabled.
                response = await client.post(endpoint, json=payload, headers=headers)
                response.raise_for_status()
            except httpx.TimeoutException:
                raise _failure("timeout", count) from None
            except httpx.HTTPStatusError as exc:
                status = getattr(exc.response, "status_code", None)
                raise _failure("http_status", count, status if type(status) is int else None) from None
            except Exception:
                raise _failure("transport", count) from None
            try:
                body = response.json()
            except Exception:
                raise _failure("invalid_json", count) from None
        try:
            return _vectors(body, count, self.dimensions)
        except ValueError:
            _log_failure("invalid_response", count)
            raise

    async def aembed_multimodal(self, inputs: list[EmbeddingInput]) -> list[list[float]]:
        """Embed complete ordered inputs; bound batch size, tasks, in-flight requests and encoded media."""
        if not inputs:
            return []
        # Decode/validate ALL media before any outbound call, off the event loop; nothing is kept.
        await asyncio.to_thread(_validate, inputs, self.max_media_bytes)
        from core.proxy.ssrf_safety import _safe_async_client
        vectors: list[list[float]] = []
        window_size = self.batch_size * self.max_concurrency
        async with contextlib.AsyncExitStack() as stack:
            # One SSRF-safe client per invocation: pooled across windows, closed on any exit.
            try:
                client = await stack.enter_async_context(
                    _safe_async_client(timeout=self.timeout_seconds, follow_redirects=False))
            except Exception:
                raise _failure("transport", 0) from None
            for start in range(0, len(inputs), window_size):
                # Encode only this window; its payloads are released before the next one.
                documents = await asyncio.to_thread(_serialize, inputs[start:start + window_size])
                results = await asyncio.gather(*(
                    self._request(client, documents[i:i + self.batch_size])
                    for i in range(0, len(documents), self.batch_size)
                ), return_exceptions=True)
                del documents
                # Every task in this bounded window is awaited even if one fails.
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
                    vectors.extend(result)
                del results
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
