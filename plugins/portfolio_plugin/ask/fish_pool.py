"""In-memory, TTL-capped staging area for recommended projects (the fishpool).

Stashes recommended-but-not-yet-spawned project candidates so that subsequent
visitor turns can retrieve and spawn them without re-evaluating recommendations.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

_MAX_POOLS = 128

# pool_id -> {pool_id, session_id, tenant_id, projects, created_at}
_pools: dict[str, dict[str, Any]] = {}


def _ask_settings() -> dict[str, Any]:
    """Read ask settings lazily from plugin SETTINGS."""
    try:
        from plugins.portfolio_plugin.plugin_config import SETTINGS

        cfg = SETTINGS.get("ask") if isinstance(SETTINGS, dict) else None
        return dict(cfg) if isinstance(cfg, dict) else {}
    except (ImportError, TypeError, ValueError, KeyError, AttributeError):
        return {}


def _fish_pool_settings() -> dict[str, Any]:
    """Read fish_pool settings dict from ask settings."""
    cfg = _ask_settings().get("fish_pool")
    return dict(cfg) if isinstance(cfg, dict) else {}


def _ttl_s() -> float:
    """Read fish_pool TTL in seconds (default 600s)."""
    try:
        return max(1.0, float(_fish_pool_settings().get("ttl_s", 600)))
    except (TypeError, ValueError):
        return 600.0


def _max_items() -> int:
    """Read fish_pool max items per pool (default 8)."""
    try:
        return max(1, int(_fish_pool_settings().get("max_items", 8)))
    except (TypeError, ValueError):
        return 8


def _tenant_int(raw: Any) -> int:
    """Coerce a context tenant id to int; never raise into a caller."""
    try:
        return int(raw) if raw is not None else 1
    except (TypeError, ValueError):
        return 1


def _evict(now: float) -> None:
    """Drop expired pools; hard-cap the table."""
    ttl = _ttl_s()
    stale = [pid for pid, rec in _pools.items() if (now - rec["created_at"]) >= ttl]
    for pid in stale:
        _pools.pop(pid, None)
    if len(_pools) >= _MAX_POOLS:
        oldest = sorted(_pools.items(), key=lambda kv: kv[1]["created_at"])
        for pid, _ in oldest[: len(_pools) - _MAX_POOLS + 1]:
            _pools.pop(pid, None)


def _drop_session_pools(session_id: str) -> None:
    """Replace-on-stash: one live pool per visitor session."""
    sid = str(session_id or "").strip()
    if not sid:
        return
    stale = [pid for pid, rec in _pools.items() if rec.get("session_id") == sid]
    for pid in stale:
        _pools.pop(pid, None)


def stash_pool(session_id: str, projects: list[dict], *, tenant_id: int = 1) -> str:
    """Stage recommended-but-unspawned projects; returns a pool_id."""
    now = time.time()
    _evict(now)
    _drop_session_pools(session_id)

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    for p in projects or []:
        if not isinstance(p, dict):
            continue
        slug = str(p.get("slug") or "").strip()
        if not slug:
            continue
        slug_key = slug.lower()
        if slug_key in seen:
            continue
        seen.add(slug_key)

        item = dict(p)
        name = str(p.get("name") or slug).strip() or slug
        summary = str(p.get("summary") or p.get("blurb") or "").strip()

        raw_tags = p.get("tags")
        tags = [str(t) for t in raw_tags if t] if isinstance(raw_tags, list) else []
        if "pooled" not in {t.lower() for t in tags}:
            tags.append("pooled")

        metrics = list(p["metrics"]) if isinstance(p.get("metrics"), list) else []
        links = list(p["links"]) if isinstance(p.get("links"), list) else []
        context_sources = list(p["context_sources"]) if isinstance(p.get("context_sources"), list) else []

        item["slug"] = slug
        item["name"] = name
        item["summary"] = summary
        item["tags"] = tags
        item["metrics"] = metrics
        item["links"] = links
        item["context_sources"] = context_sources
        item["virtual"] = True

        normalized.append(item)

    max_items = _max_items()
    if max_items > 0:
        normalized = normalized[:max_items]

    pool_id = uuid.uuid4().hex[:16]
    _pools[pool_id] = {
        "pool_id": pool_id,
        "session_id": str(session_id or ""),
        "tenant_id": _tenant_int(tenant_id),
        "projects": normalized,
        "created_at": now,
    }
    return pool_id


def get_pool(pool_id: str) -> dict:
    """{pool_id, projects, created_at} or {} if unknown/expired."""
    now = time.time()
    _evict(now)
    pid = str(pool_id or "").strip()
    rec = _pools.get(pid)
    if rec is None:
        return {}
    return {
        "pool_id": rec["pool_id"],
        "projects": [dict(p) for p in rec["projects"]],
        "created_at": rec["created_at"],
    }


def take_from_pool(pool_id: str, slugs: list[str]) -> list[dict]:
    """Pull only the requested slugs out of the pool (does NOT remove them). [] on unknown/expired."""
    now = time.time()
    _evict(now)
    if not slugs or not isinstance(slugs, list):
        return []
    pid = str(pool_id or "").strip()
    rec = _pools.get(pid)
    if rec is None:
        return []
    pool_map = {
        str(p.get("slug") or "").strip().lower(): p
        for p in rec.get("projects", [])
        if isinstance(p, dict) and p.get("slug")
    }
    out: list[dict[str, Any]] = []
    for s in slugs:
        key = str(s or "").strip().lower()
        if key and key in pool_map:
            out.append(dict(pool_map[key]))
    return out


def take_from_session(session_id: str, slugs: list[str]) -> list[dict]:
    """Pull requested slugs from the newest live pool for this visitor session."""
    now = time.time()
    _evict(now)
    sid = str(session_id or "").strip()
    if not sid or not slugs or not isinstance(slugs, list):
        return []
    matches = [rec for rec in _pools.values() if rec.get("session_id") == sid]
    if not matches:
        return []
    newest = max(matches, key=lambda rec: rec["created_at"])
    return take_from_pool(str(newest["pool_id"]), slugs)


def reset_pools() -> None:
    """Clear the pool table (tests)."""
    _pools.clear()
