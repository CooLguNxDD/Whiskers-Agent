"""LLM provider registry and factories.

Replaces ``core/llm_provider.py``. Chat + embeddings client construction is
now dynamic: each provider registers a ``ProviderSpec`` (id, factories, env
key, availability check) into a process-level ``LLMProviderRegistry`` instead
of being hardcoded into a static factory dict. See ``.claude/skills/llm-provider-enum``
for "how do I add a new provider".

Public API (unchanged names from the old module, for a drop-in import-path swap):
    LLMProvider, get_chat_llm, make_llm, make_embeddings, llm_available
Registry API (new):
    ProviderSpec, LLMProviderRegistry, get_llm_provider_registry, register_provider
"""

from core.llm_provider_management.enum_types import CLI_PROVIDERS, LLMProvider
from core.llm_provider_management.factory import (
    _CLI_PROVIDERS,
    _LLM_AVAILABLE,
    _llm_available,
    _make_llm,
    _PROVIDER_ENV_KEY,
    default_embed_for,
    get_chat_llm,
    llm_available,
    make_embeddings,
    make_llm,
)
from core.llm_provider_management.registry import (
    LLMProviderRegistry,
    get_llm_provider_registry,
    register_provider,
)
from core.llm_provider_management.spec import ProviderSpec

__all__ = [
    "LLMProvider",
    "CLI_PROVIDERS",
    "ProviderSpec",
    "LLMProviderRegistry",
    "get_llm_provider_registry",
    "register_provider",
    "get_chat_llm",
    "make_llm",
    "make_embeddings",
    "default_embed_for",
    "llm_available",
    # Back-compat private-name aliases (old core/llm_provider.py surface):
    "_llm_available",
    "_LLM_AVAILABLE",
    "_make_llm",
    "_PROVIDER_ENV_KEY",
    "_CLI_PROVIDERS",
]
