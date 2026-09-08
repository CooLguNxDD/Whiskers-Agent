"""``LLMProviderRegistry`` — process-level registry of ``ProviderSpec`` entries.

Mirrors the established registry shape used elsewhere in ``core``
(``core.scope_management.registration.PermissionRegistry``,
``core.memory.registry.MemoryRegistry``): a plain dict guarded by a lock, seeded
at first use, exposed via a lazy double-checked-locking module singleton.

Seeding imports ``providers`` (openai/anthropic/gemini/gemini-vertex) and
``cli_providers`` (claude-cli/agy-cli/grok-cli) exactly once — each module calls
``register_provider(spec)`` at import time.
"""

from __future__ import annotations

import threading

from core.llm_provider_management.spec import ProviderSpec


class LLMProviderRegistry:
    """In-memory ``provider_id -> ProviderSpec`` map."""

    def __init__(self) -> None:
        """Initialize the empty provider map and lock."""
        self._lock = threading.Lock()
        self._providers: dict[str, ProviderSpec] = {}

    def register(self, spec: ProviderSpec) -> None:
        """Register (or replace) a provider spec by its id."""
        with self._lock:
            self._providers[spec.id] = spec

    def unregister(self, provider_id: str) -> None:
        """Remove a provider spec, if present."""
        with self._lock:
            self._providers.pop(provider_id, None)

    def get(self, provider_id: str) -> ProviderSpec | None:
        """Return the spec for ``provider_id``, or None if unregistered."""
        with self._lock:
            return self._providers.get(provider_id)

    def is_registered(self, provider_id: str) -> bool:
        """True if ``provider_id`` has a registered spec."""
        with self._lock:
            return provider_id in self._providers

    def all(self) -> list[ProviderSpec]:
        """Return all registered specs."""
        with self._lock:
            return list(self._providers.values())

    def ids(self) -> set[str]:
        """Return all registered provider ids."""
        with self._lock:
            return set(self._providers.keys())

    def clear(self) -> None:
        """Test hook: drop all registered specs."""
        with self._lock:
            self._providers.clear()


_registry: LLMProviderRegistry | None = None
_registry_lock = threading.Lock()
_seeded = False
_seed_lock = threading.Lock()


def _ensure_registry() -> LLMProviderRegistry:
    """Return the singleton instance, creating it if needed (no seeding).

    Split from ``get_llm_provider_registry`` so ``register_provider`` (called
    by provider modules *during* seeding) never re-enters the seed step —
    that would recurse: seed -> import providers -> register_provider ->
    get_llm_provider_registry -> seed -> ...
    """
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            _registry = LLMProviderRegistry()
        return _registry


def _seed_builtin_providers() -> None:
    """Import provider modules once so their module-level ``register()`` calls run."""
    global _seeded
    if _seeded:
        return
    with _seed_lock:
        if _seeded:
            return
        import core.llm_provider_management.providers  # noqa: F401
        import core.llm_provider_management.cli_providers  # noqa: F401
        _seeded = True


def get_llm_provider_registry() -> LLMProviderRegistry:
    """Lazy singleton ``LLMProviderRegistry``, seeded with all built-in providers.

    Deliberately module-level (not owned by any one consumer) so chat, embedding,
    and CLI dispatch all resolve through the same instance.
    """
    reg = _ensure_registry()
    _seed_builtin_providers()
    return reg


def _set_llm_provider_registry(reg: LLMProviderRegistry | None) -> None:
    """Test hook: replace or clear the singleton (forces reseed on next get)."""
    global _registry, _seeded
    with _registry_lock:
        _registry = reg
    with _seed_lock:
        _seeded = False


def register_provider(spec: ProviderSpec) -> None:
    """Module-level convenience: register a spec directly into the singleton.

    Providers call this at import time. Deliberately uses ``_ensure_registry``
    (not ``get_llm_provider_registry``) to avoid re-entering the seed step —
    see ``_ensure_registry`` docstring.
    """
    _ensure_registry().register(spec)
