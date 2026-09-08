"""Unit tests for the pure token-mapping logic in core_050_whiskers_scope_rename.

Mirrors test_core_048_sandbox_scope.py's pattern: no DB needed (the DB-side
execution is exercised by scripts/run_tests.py's alembic upgrade-to-head step).
Cross-checks the migration's inlined prefixes against the shipped core scope
vocabulary in core/scope_management/defaults/core_scopes.json — the migration
duplicates the rename rather than importing it, since core_0NN migrations never
import from core/.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "migrations" / "versions" / "core" / "core_050_whiskers_scope_rename.py"
_CORE_SCOPES_PATH = _REPO_ROOT / "core" / "scope_management" / "defaults" / "core_scopes.json"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("core_050_whiskers_scope_rename", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration():
    return _load_migration_module()


@pytest.fixture(scope="module")
def core_scope_tokens():
    raw = json.loads(_CORE_SCOPES_PATH.read_text(encoding="utf-8"))
    entries = raw["scopes"] if isinstance(raw, dict) else raw
    return [e["token"] if isinstance(e, dict) else e for e in entries]


def test_shipped_vocabulary_carries_no_legacy_domain(core_scope_tokens, migration):
    """The default vocabulary must already be on the new domain."""
    assert core_scope_tokens, "core_scopes.json yielded no tokens"
    assert not [t for t in core_scope_tokens if t.startswith(migration._OLD_PREFIX)]


def test_upgrade_maps_every_shipped_whiskers_token_back_and_forth(core_scope_tokens, migration):
    """Round-trip: new -> old -> new is identity for every shipped token."""
    whiskers_tokens = [t for t in core_scope_tokens if t.startswith(migration._NEW_PREFIX)]
    assert whiskers_tokens, "expected the console vocabulary to live under core:whiskers"
    legacy = migration._reverse_map_list(whiskers_tokens)
    assert all(t.startswith(migration._OLD_PREFIX) for t in legacy)
    assert migration._map_list(legacy) == whiskers_tokens


def test_map_list_rewrites_bare_and_sub_domains(migration):
    assert migration._map_list(["core:tunnel:write", "core:tunnel.proxy:read"]) == [
        "core:whiskers:write",
        "core:whiskers.proxy:read",
    ]


def test_map_list_passes_through_everything_else(migration):
    untouched = ["admin", "all", "*", "core:graph:read", "core:terminal:write", "plugin:p", "group:p:t", "op:p:o"]
    assert migration._map_list(untouched) == untouched


def test_map_list_does_not_touch_a_longer_sibling_domain(migration):
    """``core:tunnelling`` is a different domain, not a ``core:tunnel`` sub-domain."""
    assert migration._map_list(["core:tunnelling:read"]) == ["core:tunnelling:read"]


def test_map_list_dedupes(migration):
    assert migration._map_list(["core:tunnel.proxy:read", "core:whiskers.proxy:read"]) == [
        "core:whiskers.proxy:read",
    ]


def test_reverse_map_list_inverts(migration):
    assert migration._reverse_map_list(["core:whiskers.analytics:read"]) == ["core:tunnel.analytics:read"]


def test_round_trip_is_lossless(migration):
    tokens = ["core:tunnel.console:read", "admin", "plugin:jules_plugin", "core:tunnel:write"]
    assert migration._reverse_map_list(migration._map_list(tokens)) == tokens
