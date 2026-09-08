"""Permission registration API — the front door for plugin/proxy scope vocab.

Plugins and proxies register their requested permission tokens here (either
declaratively via manifest ``"scopes"`` or programmatically via
``PluginContext.contribute_scopes``); the ScopeManager and vocabulary layer
read from this registry instead of a hardcoded token list.

``core.plugin_loader.scope_registry`` is kept as a thin backward-compat shim
delegating to this module — do not add a second source of truth there.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("whiskers.plugins")

_SEG = r"[a-zA-Z0-9_-]+"
_PLUGIN_RE = re.compile(rf"^plugin:({_SEG})(?::(?:read|write))?$")
_GROUP_RE = re.compile(rf"^group:({_SEG}):(\S+)$")
_OP_RE = re.compile(rf"^op:({_SEG}):(\S+)$")

RESERVED_CORE_PLUGIN_ID = "core"


@dataclass(frozen=True)
class ScopePermission:
    """A single requested scope token contributed by a plugin/proxy."""

    token: str
    description: str = ""
    access: Optional[str] = None  # e.g. "read" | "write" — optional metadata

    def to_dict(self) -> dict:
        """Legacy ``{token, description}`` shape (+ ``access`` when set)."""
        out = {"token": self.token, "description": self.description}
        if self.access:
            out["access"] = self.access
        return out


def normalize_scope_entries(plugin_id: str, raw) -> list[ScopePermission]:
    """Normalize manifest/programmatic scope entries into ScopePermission list."""
    if not raw or not isinstance(raw, list):
        return []
    entries: list[ScopePermission] = []
    for item in raw:
        if isinstance(item, str):
            entries.append(ScopePermission(token=item))
        elif isinstance(item, dict):
            token = item.get("token")
            if isinstance(token, str) and token:
                entries.append(
                    ScopePermission(
                        token=token,
                        description=str(item.get("description") or ""),
                        access=item.get("access") or None,
                    )
                )
        elif isinstance(item, ScopePermission):
            entries.append(item)
    return entries


def validate_scope_token(plugin_id: str, token: str) -> bool:
    """Return True when token is a valid plugin-declared scope for this plugin.

    Anti-squatting: a plugin may only declare ``plugin:<own-id>[:read|write]``,
    ``group:<own-id>:<tag>``, or ``op:<own-id>:<operation_id>`` tokens for
    itself. ``core:`` is reserved — only the process-seeded ``"core"`` plugin
    id (``RESERVED_CORE_PLUGIN_ID``) may register ``core:*`` tokens; any real
    plugin attempting to declare one is rejected here.
    """
    if not plugin_id or not token:
        return False

    if token.startswith("core:"):
        return plugin_id == RESERVED_CORE_PLUGIN_ID

    if plugin_id == RESERVED_CORE_PLUGIN_ID:
        # The reserved core id may only ever register core: tokens.
        return False

    plugin_match = _PLUGIN_RE.match(token)
    if plugin_match:
        return plugin_match.group(1) == plugin_id

    group_match = _GROUP_RE.match(token)
    if group_match:
        declared_id, tag = group_match.group(1), group_match.group(2)
        return declared_id == plugin_id and bool(tag)

    op_match = _OP_RE.match(token)
    if op_match:
        declared_id, op_id = op_match.group(1), op_match.group(2)
        return declared_id == plugin_id and bool(op_id)

    return False


from core.interfaces import IPermissionRegistry

class PermissionRegistry(IPermissionRegistry):
    """Process-level registry of plugin/proxy-declared scope permissions."""

    def __init__(self) -> None:
        """Initialize the empty per-plugin permission map and lock."""
        self._lock = threading.Lock()
        self._permissions: dict[str, list[ScopePermission]] = {}
        self._synthetic: set[str] = set()


    def register_plugin_permissions(
        self, plugin_id: str, entries, *, replace: bool = True
    ) -> list[ScopePermission]:
        """Normalize, validate, and store scope entries for a plugin.

        ``replace=True`` (default) overwrites any prior entries for this
        plugin — used for the declarative manifest seed. ``replace=False``
        merges with (dedupes against) existing entries — used by the
        programmatic ``scopes.contribute`` lifecycle hook so it composes with
        the manifest seed instead of clobbering it.
        """
        if not plugin_id:
            return []
        valid: list[ScopePermission] = []
        for entry in normalize_scope_entries(plugin_id, entries or []):
            if validate_scope_token(plugin_id, entry.token):
                valid.append(entry)
            else:
                logger.warning(
                    "Skipping invalid scope token '%s' for plugin '%s'",
                    entry.token,
                    plugin_id,
                )
        with self._lock:
            if replace:
                self._permissions[plugin_id] = valid
            else:
                existing = list(self._permissions.get(plugin_id, []))
                seen = {e.token for e in existing}
                for entry in valid:
                    if entry.token not in seen:
                        existing.append(entry)
                        seen.add(entry.token)
                self._permissions[plugin_id] = existing
            return list(self._permissions[plugin_id])

    def unregister_plugin_permissions(self, plugin_id: Optional[str] = None) -> None:
        """Clear a specific plugin's permissions, or all *real plugins'* if plugin_id is None.

        A full clear (``plugin_id=None``) never wipes the reserved ``"core"``
        namespace — ``"core"`` is not a plugin, it is the level-1
        core-platform vocabulary seeded once at process start
        (``seed_core_scopes``), and nothing else ever re-registers it.
        Production boot calls this with no args on every plugin
        (re)discovery pass (``core.plugin_loader.scope_registry.clear()``,
        aliased ``clear_plugin_scopes()`` in ``lifecycle_manager.py``) — a
        literal full wipe there would permanently zero ``core:*`` tokens
        for the rest of the process the moment the first discovery pass
        runs, well before any plugin ever re-seeds them (nothing does).
        """
        with self._lock:
            if plugin_id:
                self._permissions.pop(plugin_id, None)
                self._synthetic.discard(plugin_id)
            else:
                core_entries = self._permissions.get(RESERVED_CORE_PLUGIN_ID)
                self._permissions.clear()
                self._synthetic.clear()
                if core_entries:
                    self._permissions[RESERVED_CORE_PLUGIN_ID] = core_entries

    def get_plugin_permissions(self, plugin_id: str) -> list[ScopePermission]:
        """Return stored permissions for plugin ([] if none)."""
        if not plugin_id:
            return []
        with self._lock:
            return list(self._permissions.get(plugin_id, []) or [])

    def all_permissions(self) -> list[dict]:
        """Return all permission entries with a plugin_id key on each row."""
        with self._lock:
            snapshot = {k: list(v) for k, v in self._permissions.items()}
        result: list[dict] = []
        for pid, entries in snapshot.items():
            for entry in entries:
                result.append({**entry.to_dict(), "plugin_id": pid})
        return result

    def all_tokens(self) -> list[str]:
        """Return deduplicated scope tokens across all plugins."""
        with self._lock:
            snapshot = {k: list(v) for k, v in self._permissions.items()}
        seen: set[str] = set()
        tokens: list[str] = []
        for entries in snapshot.values():
            for entry in entries:
                if entry.token not in seen:
                    seen.add(entry.token)
                    tokens.append(entry.token)
        return tokens

    def fingerprint(self, plugin_id: str) -> str:
        """Stable sha256 over this plugin's normalized, sorted entries.

        Used to detect a manifest scope change across a plugin hot-swap/
        reload so persisted vocab (Phase 5) can be resynced.
        """
        entries = sorted(
            (e.token, e.description, e.access or "")
            for e in self.get_plugin_permissions(plugin_id)
        )
        payload = json.dumps(entries, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def clear(self, plugin_id: Optional[str] = None) -> None:
        """Alias of unregister_plugin_permissions (legacy-shaped name)."""
        self.unregister_plugin_permissions(plugin_id)

    def mark_synthetic(self, plugin_id: str) -> None:
        """Flag ``plugin_id`` as running on the synthesized scope floor
        (no manifest ``"scopes"`` block, no ``contribute_scopes`` call) —
        surfaced by ``run_boot_scope_health`` so it stays visible rather
        than a silent admin-only lockout."""
        if plugin_id:
            with self._lock:
                self._synthetic.add(plugin_id)

    def is_synthetic(self, plugin_id: str) -> bool:
        """True if ``plugin_id``'s scopes were synthesized, not declared."""
        with self._lock:
            return plugin_id in self._synthetic

    def synthetic_plugin_ids(self) -> list[str]:
        """Plugin ids currently running on the synthesized scope floor."""
        with self._lock:
            return sorted(self._synthetic)


_registry: PermissionRegistry | None = None
_registry_lock = threading.Lock()

_CORE_SCOPES_PATH = Path(__file__).parent / "defaults" / "core_scopes.json"


def load_core_scope_definitions() -> list[ScopePermission]:
    """Read the level-1 core-scope defaults off disk.

    Never raises: a missing/malformed file logs and yields an empty list so a
    packaging mistake degrades to "no core vocabulary" rather than a boot
    crash — ``run_boot_scope_health`` catches the resulting empty
    ``get_valid_scopes()`` and warns.
    """
    try:
        raw = json.loads(_CORE_SCOPES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load core_scopes.json: %s", exc, exc_info=True)
        return []
    return normalize_scope_entries(RESERVED_CORE_PLUGIN_ID, raw.get("scopes") or [])


def seed_core_scopes(registry: "PermissionRegistry") -> None:
    """Register the level-1 core vocabulary into ``registry`` (replace=True)."""
    entries = load_core_scope_definitions()
    if entries:
        registry.register_plugin_permissions(RESERVED_CORE_PLUGIN_ID, entries, replace=True)


def get_permission_registry() -> PermissionRegistry:
    """Lazy singleton PermissionRegistry, pre-seeded with core scopes.

    Deliberately module-level (not owned by ScopeManager) so plugin
    registration can happen before the ScopeManager singleton exists, and so
    ``ScopeManager`` test resets (``_set_scope_manager(None)``) never wipe
    the vocab. Core scopes are re-seeded on every fresh instance so a test
    reset (``_set_permission_registry(None)``) never leaves the level-1
    vocabulary empty.
    """
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            _registry = PermissionRegistry()
            seed_core_scopes(_registry)
        return _registry


def _set_permission_registry(reg: PermissionRegistry | None) -> None:
    """Test hook: replace or clear the singleton."""
    global _registry
    with _registry_lock:
        _registry = reg


def register_plugin_permissions(plugin_id: str, entries, *, replace: bool = True) -> list[ScopePermission]:
    """Module-level convenience: register via the singleton registry."""
    return get_permission_registry().register_plugin_permissions(plugin_id, entries, replace=replace)


def unregister_plugin_permissions(plugin_id: Optional[str] = None) -> None:
    """Module-level convenience: unregister via the singleton registry."""
    get_permission_registry().unregister_plugin_permissions(plugin_id)
