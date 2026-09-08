"""Headless xAI Grok CLI subprocess provider spec."""

from __future__ import annotations

from core.llm_provider_management.cli_providers._shared import make_cli_is_available
from core.llm_provider_management.registry import register_provider
from core.llm_provider_management.spec import ProviderSpec


def _chat(model: str, api_key: str | None, base_url: str | None):
    """Build a CliAgentChatModel driving the ``grok`` CLI binary."""
    from core_graph.goap_agent.llm.cli_chat_model import make_cli_chat_model
    return make_cli_chat_model("grok-cli", model or "", api_key=api_key, base_url=base_url)


SPEC = ProviderSpec(
    id="grok-cli",
    is_cli=True,
    env_key=None,
    default_chat_model="",
    default_embed_model=None,  # no embeddings — set EMBED_PROVIDER explicitly
    default_embed_dimensions=None,
    chat_factory=_chat,
    embeddings_factory=None,
    # No binary-presence check implemented for grok (matches legacy behavior):
    # always unavailable under CLI_AGENT_REQUIRE_BINARY, soft-pass otherwise.
    is_available=make_cli_is_available(None, None),
)

register_provider(SPEC)
