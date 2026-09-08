"""Cloud LLM providers — importing this package registers each one.

Each sibling module calls ``register_provider(SPEC)`` at import time; this
``__init__`` just has to import them all once (done by
``registry._seed_builtin_providers``).

``voyage`` is imported first so chat providers that reuse its embeddings
factory (anthropic) can import it without ordering surprises.
"""

from core.llm_provider_management.providers import (  # noqa: F401
    voyage,  # embeddings-only; before anthropic
    openai,
    gemini,
    gemini_vertex,
    anthropic,
)

__all__ = ["openai", "anthropic", "gemini", "gemini_vertex", "voyage"]
