"""selector -> LangChain client resolution, backed by the existing llm_pool strength column.

``compile_selector`` turns a ``ModelRoleSpec`` rung's ``selector`` (an alias,
an ``effort:<level>`` token, an explicit pool-entry name, or a raw numeric
strength) into the hint string that
``core.llm_config_service.resolve_step_llm_config`` already knows how to
resolve (exact active name -> inactive name's strength -> numeric parse ->
nearest active by strength -> highest active). This module adds nothing to
that algorithm — it only adds a short-TTL snapshot of the active chat pool so
resolving an alias doesn't cost an extra per-call DB round trip, and a
``logger.warning`` when a name-type selector doesn't match anything (a typo
would otherwise *silently* buy the most expensive active model — see
``resolve_step_llm_config`` step 5).

The snapshot deliberately carries no secrets: API keys keep flowing through
``resolve_step_llm_config``'s per-call ``decrypt_api_key_column``, cached by
``core.llm.vault_registry._ENTRY_CACHE``. This is not a second secret-lifetime
surface.
"""

from __future__ import annotations

import logging
import os
import statistics
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("whiskers.core_graph.model_roles.resolver")

_ALIASES_NON_CORE = frozenset({"strongest", "strong", "balanced", "fast", "weakest"})
_EFFORT_PREFIX = "effort:"

_POOL_TTL_S = float(os.environ.get("MODEL_ROLE_POOL_TTL", "5.0"))


@dataclass(frozen=True)
class PoolSnapshot:
    """Short-lived view of the active chat pool. No secrets."""

    entries: tuple[dict, ...]  # {"name", "strength", "provider", "model"} for active chat entries
    min_strength: float
    max_strength: float
    median_strength: float
    fetched_at: float

    def by_name(self, name: str) -> dict | None:
        """Retrieve a specific LLM pool entry's config by name from the snapshot."""
        needle = (name or "").strip().lower()
        for e in self.entries:
            if e["name"].lower() == needle:
                return e
        return None


_EMPTY_SNAPSHOT = PoolSnapshot(entries=(), min_strength=0.0, max_strength=0.0, median_strength=0.0, fetched_at=0.0)

_snapshot_cache: dict[str, Any] = {"value": _EMPTY_SNAPSHOT, "expires_at": 0.0}
_effort_map_override: dict[str, str] | None = None


async def get_pool_snapshot(*, force: bool = False) -> PoolSnapshot:
    """Active chat-pool snapshot, cached for ``MODEL_ROLE_POOL_TTL`` seconds (default 5s)."""
    now = time.monotonic()
    if not force and now < _snapshot_cache["expires_at"]:
        return _snapshot_cache["value"]

    from core.llm.pool_manager import list_pool

    try:
        rows = await list_pool(kind="chat")
    except Exception:
        logger.warning("model_roles.resolver: pool snapshot fetch failed", exc_info=True)
        rows = []

    active = [r for r in (rows or []) if r.get("is_active")]
    entries = tuple(
        {
            "name": r["name"],
            "strength": float(r.get("strength") or 0.0),
            "provider": r.get("provider"),
            "model": r.get("model"),
        }
        for r in active
        if r.get("name")
    )
    strengths = [e["strength"] for e in entries] or [0.0]
    snap = PoolSnapshot(
        entries=entries,
        min_strength=min(strengths),
        max_strength=max(strengths),
        median_strength=statistics.median(strengths),
        fetched_at=now,
    )
    _snapshot_cache["value"] = snap
    _snapshot_cache["expires_at"] = now + _POOL_TTL_S
    return snap


def invalidate_pool_snapshot() -> None:
    """Force the next ``get_pool_snapshot`` call to re-fetch. Wired into pool mutations."""
    _snapshot_cache["expires_at"] = 0.0


def set_effort_map_override(effort_map: dict[str, str] | None) -> None:
    """Set (or clear) the DB-overridden effort_map (§7's operator retune knob)."""
    global _effort_map_override
    _effort_map_override = dict(effort_map) if effort_map else None


def get_effort_map() -> dict[str, str]:
    """Effective effort_map: DB override if set, else the core-shipped default."""
    if _effort_map_override is not None:
        return dict(_effort_map_override)
    from core_graph.model_roles.builtin import get_default_effort_map

    return get_default_effort_map()


def _alias_to_hint(alias: str, snap: PoolSnapshot) -> str | None:
    """Alias -> numeric strength hint string, or None for 'core'."""
    if alias == "core":
        return None
    if not snap.entries:
        return None
    if alias in ("strongest", "strong"):
        return repr(snap.max_strength)
    if alias in ("fast", "weakest"):
        return repr(snap.min_strength)
    if alias == "balanced":
        return repr(snap.median_strength)
    return None


def compile_selector(selector: str, snap: PoolSnapshot) -> str | None:
    """Compile a role-spec ``selector`` into a ``resolve_step_llm_config`` hint.

    Returns ``None`` for the ``"core"`` alias (or when an alias/effort level
    can't be resolved against an empty pool — degrades to the core model
    rather than raising). Explicit pool-entry names and raw numeric strengths
    pass through verbatim.
    """
    s = (selector or "").strip()
    if not s or s == "core":
        return None

    if s.startswith(_EFFORT_PREFIX):
        level = s[len(_EFFORT_PREFIX):].strip()
        effort_map = get_effort_map()
        alias = effort_map.get(level)
        if alias is None:
            logger.warning("model_roles.resolver: unknown effort level '%s'; using core", level)
            return None
        return _alias_to_hint(alias, snap)

    if s in _ALIASES_NON_CORE:
        return _alias_to_hint(s, snap)

    # Explicit pool-entry name or raw numeric strength — pass through, but
    # warn if it matches neither (see resolve_step_llm_config step 5: an
    # unrecognized hint silently resolves to the strongest active entry).
    if snap.by_name(s) is None:
        try:
            float(s)
        except ValueError:
            logger.warning(
                "model_roles.resolver: selector '%s' matches no active pool entry and is not "
                "numeric; will fall back to the strongest active model",
                s,
            )
    return s


def _sniff_model_name(llm: Any) -> str:
    return str(getattr(llm, "model", getattr(llm, "model_name", "unknown")))


async def resolve_role_llm(selector: str, *, fallback: Any = None) -> tuple[Any, str]:
    """Resolve a role-spec selector to ``(llm_client, model_name)``.

    Chain: ``compile_selector`` (against the cached snapshot) ->
    ``resolve_step_llm_config`` (existing name/strength resolution, secrets
    decrypted per call) -> ``get_chat_llm`` (existing FIFO client cache). Net
    DB cost per call is unchanged from today's ``resolve_step_llm`` — the
    snapshot only removes the *added* per-alias query.
    """
    from core.llm_config_service import get_graph_core_llm, resolve_step_llm_config
    from core.llm_provider_management import LLMProvider, get_chat_llm

    snap = await get_pool_snapshot()
    hint = compile_selector(selector, snap)

    if hint is None:
        llm = fallback if fallback is not None else await get_graph_core_llm()
        return llm, _sniff_model_name(llm)

    cfg = await resolve_step_llm_config({"model": hint})
    if not cfg:
        llm = fallback if fallback is not None else await get_graph_core_llm()
        return llm, _sniff_model_name(llm)

    try:
        provider = LLMProvider(cfg["provider"])
    except ValueError:
        logger.warning("model_roles.resolver: unknown provider '%s'; defaulting to openai", cfg["provider"])
        provider = LLMProvider.OPENAI

    llm = get_chat_llm(provider, cfg["model"], api_key=cfg.get("api_key"), base_url=cfg.get("base_url"))
    return llm, str(cfg["model"])
