"""Oneshot CLI policy: enable switch, active provider detection, recursion guard."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("whiskers.core_graph.oneshot_cli")

# Claim stamped on the injected child's minted token (see
# core_graph.goap_agent.cli_inject.mint_goap_agent_token, extra_claims
# ocat_source="goap_agent_cli"). Nested run_graph uses this as caller_source
# so oneshot does not recurse.
RECURSION_GUARD_SOURCE = "goap_agent_cli"


def oneshot_enabled() -> bool:
    """Rollback switch — default on. Set ``CLI_PROVIDER_ONESHOT=0`` to force Mode A."""
    raw = os.environ.get("CLI_PROVIDER_ONESHOT")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


async def active_core_cli_provider() -> str | None:
    """Return the active core provider id if it's a headless CLI provider, else None."""
    try:
        from core.llm_config_service import resolve_core_chat
        from core.llm_provider_management import _CLI_PROVIDERS

        sel = await resolve_core_chat()
    except Exception as exc:
        logger.warning("active_core_cli_provider: resolution failed: %s", exc)
        return None
    cli_values = {p.value for p in _CLI_PROVIDERS}
    provider = (sel or {}).get("provider")
    if isinstance(provider, str) and provider.strip().lower() in cli_values:
        return provider.strip().lower()
    return None


def agent_for_provider(provider: str) -> str:
    """Map LLM pool provider id to CLI driver name (claude|agy|grok)."""
    p = (provider or "").strip().lower()
    mapping = {
        "agy-cli": "agy",
        "agy": "agy",
        "antigravity": "agy",
        "grok-cli": "grok",
        "grok": "grok",
    }
    return mapping.get(p, "claude")
