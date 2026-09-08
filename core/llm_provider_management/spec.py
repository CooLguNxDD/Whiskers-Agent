"""``ProviderSpec`` — the single descriptor every LLM provider registers.

Replaces the scattered per-provider tables that used to live across
``core/llm_provider.py``, ``core/llm_config_service.py`` (``_DEFAULT_EMBED_MODELS``),
and ``db_layer/embeddings/embeddings_core.py`` (``_DEFAULT_MODELS``/``_DEFAULT_DIMENSIONS``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel

# (model, api_key, base_url) -> chat client
ChatFactory = Callable[[str, Optional[str], Optional[str]], "BaseChatModel"]
# (model, dimensions, api_key, base_url) -> embeddings client
EmbeddingsFactory = Callable[[str, int, Optional[str], Optional[str]], "Embeddings"]
AvailabilityCheck = Callable[[], bool]


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the registry needs to construct and describe one provider.

    ``embeddings_factory=None`` means the provider has no embeddings API
    (e.g. ``agy-cli`` / ``grok-cli``). Callers must set ``EMBED_PROVIDER`` to a
    provider that has a factory — there is no silent OpenAI fallback.
    ``anthropic`` / ``claude-cli`` use Voyage AI via their own factory.
    """

    id: str
    is_cli: bool = False
    env_key: Optional[str] = None
    default_chat_model: str = ""
    default_embed_model: Optional[str] = None
    default_embed_dimensions: Optional[int] = None
    chat_factory: Optional[ChatFactory] = None
    embeddings_factory: Optional[EmbeddingsFactory] = None
    is_available: Optional[AvailabilityCheck] = None
