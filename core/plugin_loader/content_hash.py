"""
Plugin / proxy content hashing and lightweight version-history helpers.

Pure stdlib utilities used by the plugin loader, proxy manager, and DB registry
to detect source-tree drift and record OTA / hot-swap version signals.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("whiskers.plugins")

_PRUNE_DIR_NAMES = frozenset({"__pycache__", ".git", "node_modules"})
_HASHABLE_NAMES = frozenset({"manifest.json", "config.json"})
_HASHABLE_SUFFIXES = (".py", ".md")
_CHUNK_SIZE = 64 * 1024


def iter_hashable_files(package_dir: str | Path) -> list[Path]:
    """Return sorted hashable files under ``package_dir`` (POSIX relpath order).

    Includes ``*.py``, ``*.md``, ``manifest.json``, and ``config.json``.
    Prunes ``__pycache__``, ``.git``, ``node_modules``; excludes ``*.pyc``.
    """
    root = Path(package_dir)
    if not root.is_dir():
        return []

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIR_NAMES]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            if name in _HASHABLE_NAMES or name.endswith(_HASHABLE_SUFFIXES):
                found.append(Path(dirpath) / name)

    found.sort(key=lambda p: p.relative_to(root).as_posix())
    return found


def compute_plugin_tree_hash(package_dir: str | Path) -> str:
    """Compute a deterministic ``sha256:<hex>`` over the plugin source tree.

    For each hashable file (sorted by POSIX relpath): update with
    ``relpath + b"\\0"`` then raw file bytes (64 KiB chunks) then ``b"\\0"``.
    Unreadable files contribute the path with empty content and a warning so a
    permission blip does not fabricate a phantom version change.
    """
    root = Path(package_dir)
    digest = hashlib.sha256()
    for path in iter_hashable_files(root):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8") + b"\0")
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError as exc:
            # Fold path+error into the digest so permission tampering changes
            # the fingerprint (non-fatal; still produces a stable hash).
            logger.warning(
                "content_hash: unreadable %s (%s) — hashing error sentinel",
                path,
                exc,
            )
            digest.update(f"<unreadable:{rel}:{type(exc).__name__}>".encode("utf-8"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def compute_proxy_hash(identity: dict[str, Any]) -> str:
    """Hash a proxy identity tuple (never secrets / bearer tokens).

    Canonical JSON over ``{name, url, transport, auth_mode, custom_description, workspace_label}``
    with sorted keys. Editing ``custom_description`` or ``workspace_label`` counts as a new version.
    """
    payload = {
        "name": identity.get("name", "") or "",
        "url": identity.get("url", "") or "",
        "transport": identity.get("transport", "") or "",
        "auth_mode": identity.get("auth_mode", "") or "",
        "custom_description": identity.get("custom_description", "") or "",
        "workspace_label": identity.get("workspace_label", "") or "",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def append_version_history(
    meta: dict[str, Any] | None,
    version: str,
    content_hash: str,
    cap: int = 20,
) -> dict[str, Any]:
    """Append a version-history entry when ``content_hash`` differs from the last.

    Pure: returns a new meta dict. No-op (no new entry) when the last entry
    already has the same hash. Trims history to ``cap`` (default 20).
    """
    out = dict(meta or {})
    history = list(out.get("version_history") or [])
    if history and history[-1].get("content_hash") == content_hash:
        out["version_history"] = history
        return out

    history.append(
        {
            "version": version or "0.0.0",
            "content_hash": content_hash,
            "seen_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if cap > 0 and len(history) > cap:
        history = history[-cap:]
    out["version_history"] = history
    return out
