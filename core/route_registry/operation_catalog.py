"""OperationCatalog — live in-memory operation contract store.

Versioned, owner-atomic publish; entitlement filtering for catalogs.
Not backed by route_embeddings (those are eventually consistent search ranks).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Iterable

from core.route_registry.operation_descriptor import (
    OperationDescriptor,
    Visibility,
    validate_operation,
)

logger = logging.getLogger("whiskers")

_catalog: OperationCatalog | None = None
_catalog_lock = threading.Lock()


class OperationCatalog:
    """Thread-safe live catalog of OperationDescriptors keyed by (plugin_id, operation_id)."""

    def __init__(self) -> None:
        """Initialize empty catalog at revision 0."""
        self._ops: dict[tuple[str, str], OperationDescriptor] = {}
        self._revision: int = 0
        self._lock = threading.Lock()

    @property
    def revision(self) -> int:
        """Monotonic catalog revision (bumps on publish/remove)."""
        return self._revision

    @property
    def etag(self) -> str:
        """Weak ETag for HTTP caching / TanStack invalidation."""
        with self._lock:
            parts = [str(self._revision)] + sorted(
                op.descriptor_hash for op in self._ops.values()
            )
        digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
        return f'"{digest}"'

    def clear(self) -> None:
        """Drop all operations and bump revision if non-empty."""
        with self._lock:
            had = bool(self._ops)
            self._ops.clear()
            if had:
                self._revision += 1

    def publish_owner(self, plugin_id: str, ops: Iterable[OperationDescriptor]) -> int:
        """Atomically replace all operations for ``plugin_id`` with ``ops``.

        Validates the full set first; on failure the previous owner set remains.
        Bumps revision once after a successful swap.
        """
        if not plugin_id:
            raise ValueError("plugin_id is required")
        prepared: list[OperationDescriptor] = []
        for op in ops:
            validate_operation(op, owner_plugin_id=plugin_id)
            prepared.append(op)

        with self._lock:
            # Remove existing owner keys then insert new set under one lock.
            stale = [k for k in self._ops if k[0] == plugin_id]
            for k in stale:
                del self._ops[k]
            for op in prepared:
                self._ops[op.key] = op
            self._revision += 1
            count = len(prepared)

        logger.info(
            "OperationCatalog: publish_owner plugin=%s count=%d revision=%d",
            plugin_id, count, self._revision,
        )
        return count

    def remove_owner(self, plugin_id: str) -> int:
        """Host-enforced cleanup: drop every op owned by ``plugin_id``."""
        if not plugin_id:
            return 0
        with self._lock:
            keys = [k for k in self._ops if k[0] == plugin_id]
            for k in keys:
                del self._ops[k]
            removed = len(keys)
            if removed:
                self._revision += 1
        if removed:
            logger.info(
                "OperationCatalog: remove_owner plugin=%s removed=%d revision=%d",
                plugin_id, removed, self._revision,
            )
        return removed

    def get(self, plugin_id: str, operation_id: str) -> OperationDescriptor | None:
        """Resolve strictly by (plugin_id, operation_id)."""
        if not plugin_id or not operation_id:
            return None
        with self._lock:
            return self._ops.get((plugin_id, operation_id))

    def all(self) -> list[OperationDescriptor]:
        """Snapshot of all registered operations."""
        with self._lock:
            return list(self._ops.values())

    def ops_for_plugin(self, plugin_id: str) -> list[OperationDescriptor]:
        """Return operations owned by ``plugin_id``."""
        with self._lock:
            return [op for op in self._ops.values() if op.plugin_id == plugin_id]

    def filter_for_caller(
        self,
        caller_scopes: list[str] | None,
        *,
        include_hidden: bool = False,
    ) -> list[OperationDescriptor]:
        """Return ops the caller may see in a catalog (scope + visibility)."""
        from core.api_key_management.scopes import (
            is_allowed,
            required_scopes_for_route,
        )

        # Snapshot under lock; filter outside to avoid holding lock during is_allowed.
        with self._lock:
            ops = list(self._ops.values())

        out: list[OperationDescriptor] = []
        for op in ops:
            vis = op.visibility
            if isinstance(vis, Visibility):
                if vis is Visibility.HIDDEN and not include_hidden:
                    continue
            elif str(vis) == Visibility.HIDDEN.value and not include_hidden:
                continue

            if op.required_scopes:
                required = set(op.required_scopes)
            else:
                required = required_scopes_for_route(op.plugin_id, op.tags)

            if not is_allowed(caller_scopes, required):
                continue
            out.append(op)
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._ops)


def get_operation_catalog() -> OperationCatalog:
    """Process-wide OperationCatalog singleton."""
    global _catalog
    if _catalog is None:
        with _catalog_lock:
            if _catalog is None:
                _catalog = OperationCatalog()
    return _catalog


def _reset_operation_catalog_for_tests() -> None:
    """Replace the singleton (unit tests only)."""
    global _catalog
    with _catalog_lock:
        _catalog = OperationCatalog()
