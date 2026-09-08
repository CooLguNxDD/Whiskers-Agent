"""Voyage AI embeddings provider — Anthropic's recommended embed path.

Anthropic does not offer a native embeddings API. Their docs point at Voyage AI
(https://platform.claude.com/docs/en/build-with-claude/embeddings). Chat-only
providers ``anthropic`` and ``claude-cli`` import ``make_voyage_embeddings``
from this module so they never silently fall back to OpenAI embeddings.

All Voyage client logic lives here (not in ``base.py``).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from core.llm_provider_management.registry import register_provider
from core.llm_provider_management.spec import ProviderSpec

logger = logging.getLogger("whiskers")

# Defaults (Anthropic docs recommend voyage-4 family; dim 1024).
DEFAULT_MODEL = "voyage-4"
DEFAULT_DIMENSIONS = 1024
_API_DEFAULT = "https://api.voyageai.com/v1"
_BATCH_SIZE = 128

# Back-compat aliases used by anthropic / claude-cli / tests.
VOYAGE_DEFAULT_MODEL = DEFAULT_MODEL
VOYAGE_DEFAULT_DIMENSIONS = DEFAULT_DIMENSIONS


def make_voyage_embeddings(
    model: str,
    dimensions: int,
    api_key: str | None,
    base_url: str | None,
):
    """Build a LangChain-compatible Voyage AI embeddings client.

    Key resolution: explicit ``api_key`` → ``VOYAGE_API_KEY`` → ``EMBED_API_KEY``.
    Uses Voyage ``input_type`` (query vs document) rather than prompt prefixes.
    """
    from langchain_core.embeddings import Embeddings

    key = (
        api_key
        or os.environ.get("VOYAGE_API_KEY")
        or os.environ.get("EMBED_API_KEY")
        or ""
    ).strip()
    if not key:
        raise ValueError(
            "Voyage embeddings require VOYAGE_API_KEY (or EMBED_API_KEY / pool api_key). "
            "Anthropic has no native embeddings API — see "
            "https://platform.claude.com/docs/en/build-with-claude/embeddings"
        )

    m = (model or "").strip() or DEFAULT_MODEL
    dims = int(dimensions) if dimensions else DEFAULT_DIMENSIONS
    root = (base_url or os.environ.get("VOYAGE_BASE_URL") or _API_DEFAULT).rstrip("/")
    if root.endswith("/embeddings"):
        root = root[: -len("/embeddings")]
    if not root.endswith("/v1"):
        root = root.rstrip("/") + "/v1"
    endpoint = f"{root}/embeddings"

    logger.info("Embeddings: Voyage AI (%s, dim=%s)", m, dims)

    class VoyageEmbeddings(Embeddings):
        """Voyage embeddings client (httpx) with query/document input_type."""

        def __init__(self) -> None:
            self.model = m
            self.dimensions = dims
            self.api_key = key
            self.endpoint = endpoint

        def _headers(self) -> dict[str, str]:
            return {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

        def _payload(self, texts: list[str], input_type: str) -> dict[str, Any]:
            body: dict[str, Any] = {
                "input": texts,
                "model": self.model,
                "input_type": input_type,
            }
            if self.dimensions:
                body["output_dimension"] = self.dimensions
            return body

        def _post(self, texts: list[str], input_type: str) -> list[list[float]]:
            import httpx

            if not texts:
                return []
            out: list[list[float]] = []
            with httpx.Client(timeout=60.0) as client:
                for i in range(0, len(texts), _BATCH_SIZE):
                    chunk = texts[i : i + _BATCH_SIZE]
                    resp = client.post(
                        self.endpoint,
                        headers=self._headers(),
                        json=self._payload(chunk, input_type),
                    )
                    resp.raise_for_status()
                    data = resp.json().get("data") or []
                    data = sorted(data, key=lambda row: row.get("index", 0))
                    out.extend(row["embedding"] for row in data)
            return out

        async def _apost(self, texts: list[str], input_type: str) -> list[list[float]]:
            import httpx

            if not texts:
                return []
            out: list[list[float]] = []
            async with httpx.AsyncClient(timeout=60.0) as client:
                for i in range(0, len(texts), _BATCH_SIZE):
                    chunk = texts[i : i + _BATCH_SIZE]
                    resp = await client.post(
                        self.endpoint,
                        headers=self._headers(),
                        json=self._payload(chunk, input_type),
                    )
                    resp.raise_for_status()
                    data = resp.json().get("data") or []
                    data = sorted(data, key=lambda row: row.get("index", 0))
                    out.extend(row["embedding"] for row in data)
            return out

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            """Embed documents with Voyage ``input_type=document``."""
            return self._post(list(texts), "document")

        def embed_query(self, text: str) -> list[float]:
            """Embed a single query with Voyage ``input_type=query``."""
            rows = self._post([text], "query")
            return rows[0] if rows else []

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            """Async document embed with Voyage ``input_type=document``."""
            return await self._apost(list(texts), "document")

        async def aembed_query(self, text: str) -> list[float]:
            """Async query embed with Voyage ``input_type=query``."""
            rows = await self._apost([text], "query")
            return rows[0] if rows else []

    return VoyageEmbeddings()


def _embeddings(model: str, dimensions: int, api_key: str | None, base_url: str | None):
    """ProviderSpec embeddings factory for id ``voyage``."""
    return make_voyage_embeddings(model, dimensions, api_key, base_url)


def _is_available() -> bool:
    """Voyage is available when VOYAGE_API_KEY (or EMBED_API_KEY) is set."""
    return bool(
        os.environ.get("VOYAGE_API_KEY", "").strip()
        or os.environ.get("EMBED_API_KEY", "").strip()
    )


SPEC = ProviderSpec(
    id="voyage",
    is_cli=False,
    env_key="VOYAGE_API_KEY",
    default_chat_model="",
    default_embed_model=DEFAULT_MODEL,
    default_embed_dimensions=DEFAULT_DIMENSIONS,
    chat_factory=None,  # embeddings-only provider
    embeddings_factory=_embeddings,
    is_available=_is_available,
)

register_provider(SPEC)
