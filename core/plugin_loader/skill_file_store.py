"""
Read-only access to a plugin's on-disk skill markdown files.

Mirrors ``config_file_store``: the Skills tab edits DB ``meta.skills`` only;
this module loads (and hashes) the filesystem seed so the console can
"Load from disk" / detect drift. Never writes to disk.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from core.config_loader import PROJECT_ROOT
from core.plugin_loader import plugin_loader as _plugin_loader
from core.plugin_loader.resolver import _strip_yaml_frontmatter
from utils.server_config import MAX_SKILL_FILE_CHARS

logger = logging.getLogger("whiskers.plugins")


def skill_content_hash(content: str) -> str:
    """Return ``sha256:<hex>`` of UTF-8 skill body (empty string → still hashed)."""
    digest = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _safe_plugin_id(plugin_id: str) -> bool:
    """True when plugin_id is a bare directory name (no path traversal)."""
    return bool(plugin_id) and Path(plugin_id).name == plugin_id and ".." not in plugin_id


def _safe_skill_key(key: str) -> bool:
    """True when key is a relative path without traversal segments."""
    if not key or not isinstance(key, str):
        return False
    rel = key.strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return False
    # Reject absolute Windows-style and empty after normalize
    if ":" in rel.split("/")[0]:
        return False
    return True


def _plugin_dir(plugin_id: str) -> Path | None:
    """Resolved plugins/<id> directory, or None if unsafe / outside tree."""
    if not _safe_plugin_id(plugin_id):
        return None
    plugins_root = (PROJECT_ROOT / "plugins").resolve()
    plugin_dir = (plugins_root / plugin_id).resolve()
    try:
        if not plugin_dir.is_relative_to(plugins_root):
            return None
    except (ValueError, AttributeError):
        return None
    return plugin_dir


def load_skill_from_disk(plugin_id: str, key: str) -> dict:
    """Load one skill file from disk (frontmatter stripped, per-file cap).

    Returns a dict:
      ok: bool
      content: str | None
      content_hash: str | None  (sha256 of stored content when ok)
      error: str | None
      on_disk: bool
    """
    if not _safe_plugin_id(plugin_id):
        return {
            "ok": False,
            "content": None,
            "content_hash": None,
            "error": "invalid plugin_id",
            "on_disk": False,
        }
    if not _safe_skill_key(key):
        return {
            "ok": False,
            "content": None,
            "content_hash": None,
            "error": "invalid skill key",
            "on_disk": False,
        }

    plugin_dir = _plugin_dir(plugin_id)
    if plugin_dir is None:
        return {
            "ok": False,
            "content": None,
            "content_hash": None,
            "error": "invalid plugin path",
            "on_disk": False,
        }

    rel = key.strip().replace("\\", "/")
    skill_path = (plugin_dir / rel).resolve()
    try:
        if not skill_path.is_relative_to(plugin_dir):
            return {
                "ok": False,
                "content": None,
                "content_hash": None,
                "error": "path escapes plugin directory",
                "on_disk": False,
            }
    except (ValueError, AttributeError):
        return {
            "ok": False,
            "content": None,
            "content_hash": None,
            "error": "path validation failed",
            "on_disk": False,
        }

    if not skill_path.is_file():
        return {
            "ok": False,
            "content": None,
            "content_hash": None,
            "error": "skill file not found on disk",
            "on_disk": False,
        }

    try:
        raw = skill_path.read_text(encoding="utf-8")
        content = _strip_yaml_frontmatter(raw)
        if len(content) > MAX_SKILL_FILE_CHARS:
            content = content[:MAX_SKILL_FILE_CHARS].rstrip() + "\n... [truncated]"
        return {
            "ok": True,
            "content": content,
            "content_hash": skill_content_hash(content),
            "error": None,
            "on_disk": True,
        }
    except OSError as exc:
        logger.warning("skill_file_store: failed to read %s — %s", skill_path, exc)
        return {
            "ok": False,
            "content": None,
            "content_hash": None,
            "error": str(exc),
            "on_disk": True,
        }


def declared_skill_keys(plugin_id: str) -> list[str]:
    """Manifest-declared skill relative paths from the live relay cache."""
    if not _safe_plugin_id(plugin_id):
        return []
    manifest = _plugin_loader._relay_manifests.get(plugin_id) or {}
    out: list[str] = []
    for entry in manifest.get("skills") or []:
        if isinstance(entry, str) and entry.strip() and _safe_skill_key(entry):
            out.append(entry.strip().replace("\\", "/"))
    return out
