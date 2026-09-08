"""Anti-rot guard: every plugin manifest declares a real top-level "scopes" block.

Without a declared "scopes" block, `core.plugin_loader.lifecycle_manager` falls
back to a synthesized floor (`plugin:<id>`, `group:<id>:read/write`) so the
plugin stays reachable at all, and `run_boot_scope_health` flags it as
`scopes_synthetic` — but that floor is a safety net, not something a plugin
should ship on permanently. This test catches a new/edited plugin landing
without real scopes before boot health quietly starts warning about it.

Also guards against the nested `external_oauth.scopes` field (Layer-2 upstream
OAuth config) being mistaken for the top-level permission-vocabulary block —
they are unrelated axes and a plugin author reading the manifest by eye can
easily confuse them.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.scope_management.registration import validate_scope_token

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLUGINS_DIR = _REPO_ROOT / "plugins"

# Plugin manifest "name" values allowed to ship without a top-level "scopes"
# block — none today. Add an entry here only with a reviewed reason; it is
# the one legitimate way to accept the synthesized fallback floor long-term.
_UNSCOPED_EXCEPTIONS: frozenset[str] = frozenset()


def _manifest_paths() -> list[Path]:
    return sorted(_PLUGINS_DIR.glob("*/manifest.json"))


def test_every_plugin_manifest_has_top_level_scopes():
    """Every plugin manifest.json declares a non-empty top-level "scopes"."""
    paths = _manifest_paths()
    assert paths, "expected at least one plugins/*/manifest.json"

    missing: list[str] = []
    for path in paths:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        name = manifest.get("name", path.parent.name)
        if name in _UNSCOPED_EXCEPTIONS:
            continue
        scopes = manifest.get("scopes")
        if not scopes:
            missing.append(name)

    assert not missing, (
        f"plugin manifest(s) missing a top-level 'scopes' block: {missing} — "
        "add one (see plugins/portfolio_plugin/manifest.json for the shape) "
        "or add a reviewed entry to _UNSCOPED_EXCEPTIONS"
    )


def test_declared_scope_tokens_are_valid_for_their_own_plugin():
    """Every declared token passes validate_scope_token for its own plugin id.

    Catches the common typo of one plugin declaring another plugin's or a
    reserved core:* token — the anti-squat check the runtime registry
    already enforces (silently skipping the bad token with a log warning),
    surfaced here as a hard CI failure instead.
    """
    bad: list[str] = []
    for path in _manifest_paths():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        name = manifest.get("name", path.parent.name)
        scopes = manifest.get("scopes") or []
        for entry in scopes:
            token = entry.get("token") if isinstance(entry, dict) else entry
            if not token or not validate_scope_token(name, token):
                bad.append(f"{name}: {token!r}")

    assert not bad, f"invalid scope token(s) for their declaring plugin: {bad}"


def test_top_level_scopes_is_not_the_external_oauth_scopes_field():
    """A manifest's top-level "scopes" must not literally be ["ALL"] copied
    from external_oauth — that's Layer-2 upstream OAuth scope config, not a
    permission-vocabulary declaration, and would grant a meaningless token."""
    offenders: list[str] = []
    for path in _manifest_paths():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        name = manifest.get("name", path.parent.name)
        scopes = manifest.get("scopes")
        if scopes == ["ALL"]:
            offenders.append(name)

    assert not offenders, (
        f"plugin(s) with top-level scopes == ['ALL'] (external_oauth shape "
        f"leaked into the permission vocabulary): {offenders}"
    )
