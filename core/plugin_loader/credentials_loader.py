"""
Per-plugin local credentials loader.

Reads a gitignored ``credentials.json`` sidecar file from each plugin's
directory, returning a flat ``{key: value}`` dict.

Resolution priority (highest → lowest):
  1. Vault (encrypted DB, managed via terminal/script/manage_credentials.py)
  2. credentials.json  ← this module  (local dev override, gitignored)
  3. manifest.json inline values      (legacy / fallback)
  4. Empty string                     (last resort)

The file format is a simple flat JSON object.  Any key is valid — passwords,
API keys, tokens, etc.:

    {
        "username": "dev_user",
        "password": "dev_pass",
        "API_KEY":  "sk-..."
    }

Keys declared in the plugin's ``required_credentials`` / ``optional_credentials``
manifest fields map 1-to-1 to keys here.  Extra keys are silently allowed so
plugins can store any local secret without touching the manifest.

Vault seeding
-------------
``seed_vault_from_credentials()`` provides a one-way migration from the local
file into the encrypted vault.  After seeding you can empty ``credentials.json``
and the vault entry will take over at runtime.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("whiskers.plugins")


def normalize_credential_keys(raw: Any) -> list[str]:
    """Normalise manifest required/optional_credentials to plain key names.

    Accepts both legacy string entries and object form used by some plugins::

        ["API_KEY", "OTHER"]
        [{"key": "GITHUB_TOKEN", "description": "...", "required": false}]

    Returns a de-duplicated list of non-empty key strings (order preserved).
    Unknown shapes are skipped rather than raising.
    """
    if not raw:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name: str | None = None
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            key = item.get("key") or item.get("name") or item.get("id")
            if isinstance(key, str):
                name = key.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def load_plugin_credentials(plugin_dir: Path) -> dict[str, str]:
    """Load ``credentials.json`` from *plugin_dir* and return a flat string dict.

    Returns an empty dict (never raises) when:
    - The file does not exist (expected for plugins with no local creds)
    - The file contains invalid JSON
    - The top-level value is not a JSON object

    Args:
        plugin_dir: Absolute path to the plugin package directory
                    (i.e. the folder containing ``manifest.json``).

    Returns:
        Flat ``{key: str_value}`` mapping of all local credentials.
        Non-string values are coerced to str via ``str()``.
    """
    cred_path = plugin_dir / "credentials.json"

    if not cred_path.exists():
        return {}

    try:
        raw = cred_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "credentials_loader: invalid JSON in %s — %s (skipping local creds)",
            cred_path,
            exc,
        )
        return {}
    except OSError as exc:
        logger.warning(
            "credentials_loader: cannot read %s — %s (skipping local creds)",
            cred_path,
            exc,
        )
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "credentials_loader: %s top-level value is not a JSON object — skipping",
            cred_path,
        )
        return {}

    # Coerce all values to str; skip None values (treat as absent).
    result: dict[str, str] = {}
    for k, v in data.items():
        if v is None:
            continue
        result[str(k)] = str(v)

    plugin_name = plugin_dir.name
    logger.debug(
        "credentials_loader: loaded %d key(s) from %s/credentials.json",
        len(result),
        plugin_name,
    )
    return result


def get_credential(plugin_dir: Path, key: str, default: str = "") -> str:
    """Convenience wrapper: load credentials and return a single key.

    Args:
        plugin_dir: Plugin package directory (contains ``credentials.json``).
        key: Credential key to retrieve.
        default: Value to return when the key is absent or the file is missing.

    Returns:
        The credential value as a string, or *default* when not found.
    """
    return load_plugin_credentials(plugin_dir).get(key, default)


# ---------------------------------------------------------------------------
# Vault seeding
# ---------------------------------------------------------------------------


@dataclass
class SeedResult:
    """Result of a seed_vault_from_credentials() call."""

    plugin_id: str
    seeded: list[str] = field(default_factory=list)    # keys written to vault
    skipped: list[str] = field(default_factory=list)   # keys already in vault (overwrite=False)
    empty: list[str] = field(default_factory=list)     # keys present but blank in credentials.json
    errors: list[str] = field(default_factory=list)    # keys that failed to write

    @property
    def ok(self) -> bool:
        """True when no errors occurred."""
        return not self.errors

    def summary(self) -> str:
        """One-line human-readable summary."""
        parts = []
        if self.seeded:
            parts.append(f"{len(self.seeded)} seeded ({', '.join(self.seeded)})")
        if self.skipped:
            parts.append(f"{len(self.skipped)} already-in-vault ({', '.join(self.skipped)})")
        if self.empty:
            parts.append(f"{len(self.empty)} empty/skipped ({', '.join(self.empty)})")
        if self.errors:
            parts.append(f"{len(self.errors)} ERRORS ({', '.join(self.errors)})")
        return "; ".join(parts) if parts else "nothing to do"


async def seed_vault_from_credentials(
    plugin_dir: Path,
    plugin_id: str,
    vault,
    *,
    overwrite: bool = False,
    keys: list[str] | None = None,
) -> SeedResult:
    """Read ``credentials.json`` and push its values into the vault.

    This is a one-way migration helper: local file → encrypted vault.
    After seeding, the vault entry takes precedence at runtime so you
    can safely empty or delete ``credentials.json``.

    Args:
        plugin_dir: Plugin package directory (contains ``credentials.json``).
        plugin_id:  Vault namespace for this plugin (usually ``manifest["name"]``).
        vault:      A ``VaultService`` instance (from ``core.context.vault``).
        overwrite:  When ``False`` (default) existing vault keys are left unchanged.
                    When ``True`` every non-empty local value overwrites the vault entry.
        keys:       Optional allowlist of key names to seed.  When ``None`` all
                    non-comment keys from ``credentials.json`` are considered.

    Returns:
        :class:`SeedResult` with lists of seeded, skipped, empty, and errored keys.
    """
    result = SeedResult(plugin_id=plugin_id)

    local_creds = load_plugin_credentials(plugin_dir)
    if not local_creds:
        logger.debug("seed_vault: no credentials.json or empty for %s", plugin_id)
        return result

    # Filter comment/meta keys (keys starting with "_")
    candidates = {k: v for k, v in local_creds.items() if not k.startswith("_")}

    # Apply optional allowlist
    if keys is not None:
        candidates = {k: v for k, v in candidates.items() if k in keys}

    for key, value in candidates.items():
        # Skip blank values — user hasn't filled them in yet
        if not value or not value.strip():
            result.empty.append(key)
            continue

        try:
            if not overwrite:
                existing = await vault.get(plugin_id, key)
                if existing:
                    logger.debug(
                        "seed_vault: %s/%s already in vault — skipping (use overwrite=True to force)",
                        plugin_id, key,
                    )
                    result.skipped.append(key)
                    continue

            await vault.set(plugin_id, key, value)
            logger.info("seed_vault: wrote %s/%s to vault", plugin_id, key)
            result.seeded.append(key)

        except Exception as exc:  # noqa: BLE001
            logger.warning("seed_vault: failed to write %s/%s — %s", plugin_id, key, exc)
            result.errors.append(key)

    return result
