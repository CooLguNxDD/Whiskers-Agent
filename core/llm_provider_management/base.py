"""Shared helpers used by more than one provider module.

Vertex env isolation lives here. Embeddings factories live with their provider
modules under ``providers/`` (OpenAI, Gemini, Voyage) — not in this file.
"""

from __future__ import annotations

import contextlib
import os
import threading
from unittest.mock import patch

_vertex_env_lock = threading.RLock()


@contextlib.contextmanager
def isolated_vertex_env():
    """Temporarily remove GOOGLE_API_KEY/GEMINI_API_KEY during Vertex client construction.

    Prevents a plain-Gemini key (if configured) from leaking into google-genai Client
    when using gemini-vertex express mode (vertexai=True + GOOGLE_VERTEX_API_KEY).
    Uses patch.dict so the current thread sees a clean env snapshot; serialized by lock.
    """
    with _vertex_env_lock:
        with patch.dict(os.environ, clear=False) as env_copy:
            env_copy.pop("GOOGLE_API_KEY", None)
            env_copy.pop("GEMINI_API_KEY", None)
            yield
