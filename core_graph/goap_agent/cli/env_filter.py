"""Sanitize parent process env before spawning headless CLI agents.

Strips secrets (DB URLs, API keys, tokens, passwords) so LLM-driven child
processes do not inherit credentials they do not need. CLI auth keys that
the child must use for Claude/agy or the injected MCP bearer are allowlisted.
"""

from __future__ import annotations

import os
from typing import Mapping

# Exact keys always dropped from the parent env copy.
_EXACT_DENY: frozenset[str] = frozenset({
    "MASTER_KEY",
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "POSTGRES_USER",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_VERTEX_API_KEY",
    "TAVILY_API_KEY",
    "BRAVE_SEARCH_API_KEY",
    "NOTION_API_KEY",
    "GITHUB_TOKEN",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "WHISKERS_API_KEY",
    "WHISKERS_API_SECRET",
    "WHISKERS_API_TOKEN",
    "WHISKERS_API_PASSWORD",
})

# Suffixes that mark secret-like keys (case-sensitive, common env convention).
_SUFFIX_DENY: tuple[str, ...] = (
    "_API_KEY",
    "_SECRET",
    "_PASSWORD",
    "_TOKEN",
)

# Kept even when they match suffix denylist — required for CLI auth / MCP inject.
_ALLOWLIST: frozenset[str] = frozenset({
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "GOAP_AGENT_MCP_TOKEN",
    "CLI_AGENT_MCP_TOKEN",
    "GOAP_AGENT_MCP_CONFIG",
    "CLI_AGENT_MCP_CONFIG",
    "GROK_AUTH_JSON",
})

def is_secret_env_key(key: str) -> bool:
    """Return True if *key* should be stripped from the CLI child env."""
    if not key or key in _ALLOWLIST:
        return False
    if key in _EXACT_DENY:
        return True
    return any(key.endswith(suf) for suf in _SUFFIX_DENY)


def sanitized_parent_env(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy *source* (default ``os.environ``) with secret keys removed.

    Apply denylist **before** merging per-run ``options.env`` overrides so
    explicit pool-key auth and empty-value pops still work.
    """
    base = dict(source if source is not None else os.environ)
    return {k: v for k, v in base.items() if not is_secret_env_key(k)}
