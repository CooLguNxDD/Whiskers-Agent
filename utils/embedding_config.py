"""Config loader for embedding_config.json.

Loads once at import time. Exposes active embedding model prefix templates
and formatting helpers.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from utils.config_registry import get_config_registry

logger = logging.getLogger("whiskers")

EMBEDDING_CONFIG: dict[str, Any] = get_config_registry().embedding

_DEFAULT_PROFILE: dict[str, Any] = {
    "provider": "openai",
    "model": "text-embedding-3-small",
    "dimensions": 1536,
    "query_prefix": "{text}",
    "document_prefix": "{text}"
}


def get_active_profile() -> dict[str, Any]:
    """Resolve the active embedding model profile from config or environment.

    active may be overridden by env EMBED_PROFILE.
    Falls back to OpenAI symmetric ({text}) templates if the file or profile
    is missing.
    """
    active = os.environ.get("EMBED_PROFILE")
    if not active:
        active = EMBEDDING_CONFIG.get("active")

    if not active:
        return _DEFAULT_PROFILE

    models = EMBEDDING_CONFIG.get("models", {})
    profile = models.get(active)
    if not profile:
        return _DEFAULT_PROFILE

    return profile


def format_query(text: str) -> str:
    """Format the query text with the active profile's query prefix.

    Returns the raw text as a no-op if the query prefix is the bare '{text}' placeholder.
    """
    profile = get_active_profile()
    prefix = profile.get("query_prefix", "{text}")
    if prefix == "{text}":
        return text
    return prefix.format(text=text)


def format_document(text: str, title: str | None = None) -> str:
    """Format the document text with the active profile's document prefix.

    Returns the raw text as a no-op if the document prefix is the bare '{text}' placeholder.
    Tolerant of templates that do not reference '{title}'.
    """
    profile = get_active_profile()
    prefix = profile.get("document_prefix", "{text}")
    if prefix == "{text}":
        return text

    default_title = profile.get("default_title", "none")
    t_val = title or default_title
    return prefix.format(text=text, title=t_val)
